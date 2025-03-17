"""
LAUNCH BISON SPAMCHECK SCRIPT
============================
Launches scheduled Bison spamchecks by:
1. Finding pending spamchecks
2. For each spamcheck:
   - If domain-based is enabled:
     * Groups accounts by domain
     * Randomly selects one account per domain
   - If domain-based is disabled:
     * Uses all accounts as is
3. For each selected account:
   - Gets Bison account ID
   - Gets EmailGuard tag
   - Sends email using Bison API
   - Stores email info

Key Functions:
- get_emailguard_tag: Creates test and returns UUID
- get_bison_account_id: Gets Bison ID for email account
- send_bison_email: Sends email via Bison API

Critical Settings:
- Rate limit: 10 requests/second
- Email sending uses Bison's API
- Account validation before sending

Domain-based Features:
- Detects if spamcheck is domain-based
- Groups accounts by domain (@gmail.com, @outlook.com, etc.)
- Randomly selects one account per domain
- Only sends emails for selected accounts
- Other accounts with same domain get results in report phase

Runs via cron: * * * * * (every minute)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import (
    UserSpamcheckBison, 
    UserSpamcheckAccountsBison,
    SpamcheckErrorLog
)
from settings.models import UserSettings, UserBison
import aiohttp
import asyncio
import json
import logging
from datetime import timedelta
import pytz
import random
from itertools import groupby
from operator import attrgetter
from django.db import transaction
import traceback

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Launch scheduled Bison spamchecks'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second
        self.server_error_count = 0  # Counter for server errors

    def get_domain_from_email(self, email):
        """Extract domain from email address"""
        return email.split('@')[1] if '@' in email else None

    def group_accounts_by_domain(self, accounts):
        """Group accounts by domain and return a dictionary of domain -> accounts"""
        accounts_by_domain = {}
        for account in accounts:
            domain = self.get_domain_from_email(account.email_account)
            if domain:
                if domain not in accounts_by_domain:
                    accounts_by_domain[domain] = []
                accounts_by_domain[domain].append(account)
        return accounts_by_domain

    def filter_domain_accounts(self, accounts, is_domain_based):
        """Filter accounts based on domain settings"""
        if not is_domain_based:
            return accounts

        # Group accounts by domain
        accounts_by_domain = self.group_accounts_by_domain(accounts)

        # Randomly select one account per domain
        selected_accounts = []
        for domain, domain_accounts in accounts_by_domain.items():
            selected_accounts.append(random.choice(domain_accounts))

        return selected_accounts

    async def get_emailguard_tag(self, session, campaign_name, emailguard_api_key):
        """Get EmailGuard tag for campaign"""
        url = "https://app.emailguard.io/api/v1/inbox-placement-tests"
        headers = {
            "Authorization": f"Bearer {emailguard_api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "name": campaign_name,
            "type": "inbox_placement"
        }

        try:
            async with self.rate_limit:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status in [200, 201]:
                        data = await response.json()
                        return (
                            data['data']['uuid'],
                            data['data']['inbox_placement_test_emails'],
                            data['data']['filter_phrase']
                        )
                    else:
                        raise Exception(f"EmailGuard API error: {response.status}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error getting EmailGuard tag: {str(e)}"))
            raise

    async def get_bison_account_id(self, session, email_account, bison_api_key, base_url):
        """Get Bison account ID and status for email account"""
        url = f"{base_url.rstrip('/')}/api/sender-emails/{email_account}"
        headers = {
            "Authorization": f"Bearer {bison_api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with self.rate_limit:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        account_data = data['data']
                        return {
                            'id': account_data['id'],
                            'status': account_data['status'],
                            'is_connected': account_data['status'] == 'Connected'
                        }
                    else:
                        raise Exception(f"Bison API error: {response.status}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error getting Bison account ID: {str(e)}"))
            raise

    async def send_bison_email(self, session, spamcheck, account, test_emails, bison_api_key, bison_account_id, filter_phrase, base_url):
        """Send email via Bison API - one email per recipient"""
        url = f"{base_url.rstrip('/')}/api/replies/new"
        headers = {
            "Authorization": f"Bearer {bison_api_key}",
            "Content-Type": "application/json"
        }
        
        # Debug log the test_emails structure
        self.stdout.write(f"Test emails data: {test_emails}")
        
        # Format the message body as HTML for better formatting
        message_body = spamcheck.body
        
        # Add proper HTML formatting
        formatted_message = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; }}
                p {{ margin-bottom: 15px; }}
            </style>
        </head>
        <body>
            {message_body.replace(chr(10), '<br>')}
            <br><br>
            {filter_phrase}
        </body>
        </html>
        """
        
        # Track successful sends
        successful_sends = 0
        failed_sends = 0
        
        # Send one email per recipient
        for email in test_emails:
            # Prepare single recipient in the format Bison API expects
            to_email = [{
                "name": email.get('name', ''),
                "email_address": email['email']
            }]
            
            data = {
                "subject": spamcheck.subject,
                "message": formatted_message,
                "sender_email_id": bison_account_id,
                "content_type": "text" if spamcheck.plain_text else "html",
                "to_emails": to_email  # Single recipient
            }

            # Debug log the request payload
            self.stdout.write(f"Sending to recipient: {email['email']}")
            
            try:
                # Use rate limit semaphore to control request rate
                async with self.rate_limit:
                    async with session.post(url, headers=headers, json=data) as response:
                        response_text = await response.text()
                        self.stdout.write(f"Response status: {response.status}")
                        
                        if response.status == 200:
                            response_data = json.loads(response_text)
                            if response_data['data']['success']:
                                successful_sends += 1
                                self.stdout.write(f"✅ Successfully sent to {email['email']}")
                            else:
                                failed_sends += 1
                                self.stdout.write(f"❌ API returned success=false for {email['email']}: {response_text}")
                        elif response.status == 500 and "Server Error" in response_text:
                            # Increment server error count
                            self.server_error_count += 1
                            failed_sends += 1
                            self.stdout.write(self.style.WARNING(f"Bison Server Error detected (count: {self.server_error_count}) for {email['email']}"))
                            
                            # Log the error but continue with other recipients
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='server_error',
                                provider='bison',
                                error_message=f"Bison Server Error when sending email to {email['email']} for account {account.email_account}",
                                error_details={'response': response_text},
                                account_email=account.email_account,
                                step='send_bison_email',
                                api_endpoint=url,
                                status_code=response.status
                            )
                        elif response.status == 422:
                            failed_sends += 1
                            self.stdout.write(self.style.ERROR(f"Validation error from Bison API for {email['email']}: {response_text}"))
                        else:
                            failed_sends += 1
                            self.stdout.write(self.style.ERROR(f"Failed to send email to {email['email']}: {response.status} - {response_text}"))
            except Exception as e:
                failed_sends += 1
                self.stdout.write(self.style.ERROR(f"Error sending Bison email to {email['email']}: {str(e)}"))
        
        # Consider the overall operation successful if at least one email was sent successfully
        if successful_sends > 0:
            self.stdout.write(f"Successfully sent {successful_sends}/{len(test_emails)} emails")
            return True
        else:
            self.stdout.write(self.style.ERROR(f"Failed to send any emails. All {failed_sends} attempts failed."))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck"""
        try:
            self.stdout.write(f"\nProcessing Bison spamcheck {spamcheck.id}")
            
            # Update status immediately to prevent double-processing
            spamcheck.status = 'in_progress'
            await asyncio.to_thread(spamcheck.save)
            self.stdout.write(f"Updated spamcheck {spamcheck.id} status to 'in_progress'")
            
            # Reset server error counter for this spamcheck
            self.server_error_count = 0
            # Track failed domains and accounts for potential removal
            failed_domains = set()
            failed_accounts = set()
            
            # Get required settings and tokens
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )
            
            # Debug log for EmailGuard API key
            self.stdout.write(f"EmailGuard API Key exists: {bool(user_settings.emailguard_api_key)}")
            self.stdout.write(f"EmailGuard Status: {user_settings.emailguard_status}")
            
            if not user_settings.emailguard_api_key or not user_settings.emailguard_status:
                self.stdout.write(self.style.ERROR("EmailGuard is not properly configured. Please check API key and connection status."))
                
                # Log the error
                try:
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='configuration_error',
                        provider='emailguard',
                        error_message="EmailGuard is not properly configured. Please check API key and connection status.",
                        step='check_emailguard_configuration'
                    )
                except Exception as log_error:
                    self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                return False
            
            user_bison = await asyncio.to_thread(
                UserBison.objects.get,
                user=spamcheck.user,
                id=spamcheck.user_organization_id
            )

            # Use user_bison.base_url instead of user_settings.bison_base_url for consistency
            bison_base_url = user_bison.base_url
            
            # Debug log for Bison base URL and API key
            self.stdout.write(f"Bison Base URL: {bison_base_url}")
            self.stdout.write(f"Bison API Key exists: {bool(user_bison.bison_organization_api_key)}")
            self.stdout.write(f"Bison Organization Status: {user_bison.bison_organization_status}")
            
            # Get accounts to check
            all_accounts = await asyncio.to_thread(
                lambda: list(spamcheck.accounts.all())
            )

            if not all_accounts:
                self.stdout.write("No accounts to check")
                # Reset status to pending if no accounts found
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                return False

            # Group accounts by domain if domain-based is enabled
            if spamcheck.is_domain_based:
                accounts_by_domain = self.group_accounts_by_domain(all_accounts)
                self.stdout.write(f"\nDomain-based spamcheck enabled:")
                self.stdout.write(f"- Total accounts: {len(all_accounts)}")
                self.stdout.write(f"- Total domains: {len(accounts_by_domain)}")
                
                # Process each domain
                successful_accounts = 0
                total_accounts = len(all_accounts)
                
                for domain, domain_accounts in accounts_by_domain.items():
                    domain_success = False
                    domain_attempts = 0
                    domain_max_attempts = len(domain_accounts)  # Try all accounts in domain if needed
                    
                    # Try accounts in this domain until one succeeds or we run out of accounts
                    while not domain_success and domain_attempts < domain_max_attempts:
                        domain_attempts += 1
                        try:
                            # Get next account to try
                            current_account = domain_accounts[domain_attempts - 1]
                            self.stdout.write(f"\nAttempt {domain_attempts}/{domain_max_attempts} for domain {domain}")
                            self.stdout.write(f"Trying account: {current_account.email_account}")
                            
                            # Get Bison account ID and check status
                            account_info = await self.get_bison_account_id(
                                session, current_account.email_account, 
                                user_bison.bison_organization_api_key,
                                bison_base_url
                            )
                            
                            if not account_info['is_connected']:
                                self.stdout.write(f"Skipping disconnected account: {current_account.email_account}")
                                continue

                            # Get EmailGuard tag for the domain
                            campaign_name = f"{spamcheck.name} - {domain}"
                            tag, test_emails, filter_phrase = await self.get_emailguard_tag(
                                session, campaign_name, user_settings.emailguard_api_key
                            )
                            
                            self.stdout.write(f"Got EmailGuard tag for domain {domain}: {tag}")
                            
                            # Update all accounts in this domain with the same tag
                            for account in domain_accounts:
                                # Update tag in database
                                await asyncio.to_thread(
                                    lambda: UserSpamcheckAccountsBison.objects.filter(id=account.id).update(
                                        last_emailguard_tag=tag,
                                        updated_at=timezone.now()
                                    )
                                )
                            
                            # Send email using the current account
                            email_sent = await self.send_bison_email(
                                session, spamcheck, current_account, test_emails,
                                user_bison.bison_organization_api_key,
                                account_info['id'], filter_phrase,
                                bison_base_url
                            )
                            
                            if email_sent:
                                domain_success = True
                                successful_accounts += 1
                                self.stdout.write(f"✅ Successfully sent email for domain {domain} using account {current_account.email_account}")
                            else:
                                # If send_bison_email returned False (server error), try next account
                                self.stdout.write(f"❌ Failed to send email for account {current_account.email_account}, trying next account in domain")
                                failed_accounts.add(current_account.email_account)
                                
                        except Exception as e:
                            error_message = str(e)
                            self.stdout.write(self.style.ERROR(f"Error processing account {current_account.email_account}: {error_message}"))
                            failed_accounts.add(current_account.email_account)
                            
                            # Check if this is a server error that we're already tracking
                            if "Failed to send email: 500 - " in error_message and "Server Error" in error_message:
                                # Try next account in domain
                                self.stdout.write(self.style.WARNING(f"Server Error for account {current_account.email_account}, trying next account in domain"))
                                continue
                            
                            # Log the error
                            try:
                                await asyncio.to_thread(
                                    SpamcheckErrorLog.objects.create,
                                    user=spamcheck.user,
                                    bison_spamcheck=spamcheck,
                                    error_type='processing_error',
                                    provider='bison',
                                    error_message=f"Error processing account {current_account.email_account}: {error_message}",
                                    error_details={'full_error': error_message, 'traceback': traceback.format_exc()},
                                    account_email=current_account.email_account,
                                    step='process_account'
                                )
                            except Exception as log_error:
                                self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                    
                    # If all accounts in this domain failed, mark domain as failed
                    if not domain_success:
                        failed_domains.add(domain)
                        self.stdout.write(self.style.ERROR(f"❌ All accounts in domain {domain} failed. Marking domain as failed."))
                        
                        # Log the domain failure
                        try:
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='domain_failure',
                                provider='bison',
                                error_message=f"All accounts in domain {domain} failed after {domain_attempts} attempts",
                                step='process_domain'
                            )
                        except Exception as log_error:
                            self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                # Remove failed accounts from the spamcheck
                if failed_accounts:
                    self.stdout.write(f"Removing {len(failed_accounts)} failed accounts from spamcheck")
                    for account_email in failed_accounts:
                        try:
                            await asyncio.to_thread(
                                lambda: UserSpamcheckAccountsBison.objects.filter(
                                    bison_spamcheck=spamcheck,
                                    email_account=account_email
                                ).delete()
                            )
                            self.stdout.write(f"Removed account {account_email} from spamcheck")
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error removing account {account_email}: {str(e)}"))
                
                # Only fail the spamcheck if we have 5 or more server errors or all domains failed
                if self.server_error_count >= 5:
                    self.stdout.write(self.style.ERROR(f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed."))
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
                
                if len(failed_domains) == len(accounts_by_domain):
                    self.stdout.write(self.style.ERROR(f"All domains failed. Marking spamcheck as failed."))
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
                        
            else:
                # Non-domain based processing - one tag per account
                successful_accounts = 0
                total_accounts = len(all_accounts)
                
                for account in all_accounts:
                    try:
                        # Get Bison account ID and check status
                        account_info = await self.get_bison_account_id(
                            session, account.email_account,
                            user_bison.bison_organization_api_key,
                            bison_base_url
                        )

                        if not account_info['is_connected']:
                            self.stdout.write(f"Skipping disconnected account: {account.email_account}")
                            continue

                        # Get EmailGuard tag for this account
                        campaign_name = f"{spamcheck.name} - {account.email_account}"
                        tag, test_emails, filter_phrase = await self.get_emailguard_tag(
                            session, campaign_name, user_settings.emailguard_api_key
                        )

                        # Update tag in database
                        await asyncio.to_thread(
                            lambda: UserSpamcheckAccountsBison.objects.filter(id=account.id).update(
                                last_emailguard_tag=tag,
                                updated_at=timezone.now()
                            )
                        )

                        # Send email
                        email_sent = await self.send_bison_email(
                            session, spamcheck, account, test_emails,
                            user_bison.bison_organization_api_key,
                            account_info['id'], filter_phrase,
                            bison_base_url
                        )

                        if email_sent:
                            successful_accounts += 1
                        else:
                            # If send_bison_email returned False (server error), add to failed accounts
                            failed_accounts.add(account.email_account)

                    except Exception as e:
                        error_message = str(e)
                        self.stdout.write(self.style.ERROR(f"Error processing account {account.email_account}: {error_message}"))
                        failed_accounts.add(account.email_account)
                        
                        # Check if this is a server error that we're already tracking
                        if "Failed to send email: 500 - " in error_message and "Server Error" in error_message:
                            # Skip this account but continue processing others
                            self.stdout.write(self.style.WARNING(f"Skipping account {account.email_account} due to Bison Server Error"))
                            continue
                        
                        # Log the error
                        try:
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='processing_error',
                                provider='bison',
                                error_message=f"Error processing account {account.email_account}: {error_message}",
                                error_details={'full_error': error_message, 'traceback': traceback.format_exc()},
                                account_email=account.email_account,
                                step='process_account'
                            )
                        except Exception as log_error:
                            self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                # Remove failed accounts from the spamcheck
                if failed_accounts:
                    self.stdout.write(f"Removing {len(failed_accounts)} failed accounts from spamcheck")
                    for account_email in failed_accounts:
                        try:
                            await asyncio.to_thread(
                                lambda: UserSpamcheckAccountsBison.objects.filter(
                                    bison_spamcheck=spamcheck,
                                    email_account=account_email
                                ).delete()
                            )
                            self.stdout.write(f"Removed account {account_email} from spamcheck")
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error removing account {account_email}: {str(e)}"))
                        
                # Only fail the spamcheck if we have 5 or more server errors or all accounts failed
                if self.server_error_count >= 5:
                    self.stdout.write(self.style.ERROR(f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed."))
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
                
                if len(failed_accounts) == total_accounts:
                    self.stdout.write(self.style.ERROR(f"All accounts failed. Marking spamcheck as failed."))
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False

            # Only update to in_progress if at least one account was successful
            if successful_accounts > 0:
                spamcheck.status = 'waiting_for_reports'
                await asyncio.to_thread(spamcheck.save)
                return True
            else:
                self.stdout.write(self.style.ERROR(f"No accounts were successfully processed for spamcheck {spamcheck.id}"))
                
                # Log the error
                try:
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='processing_error',
                        provider='bison',
                        error_message=f"No accounts were successfully processed for spamcheck {spamcheck.id}",
                        step='process_accounts'
                    )
                except Exception as log_error:
                    self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            
            # Log the error
            try:
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='processing_error',
                    provider='bison',
                    error_message=f"Error processing spamcheck {spamcheck.id}: {str(e)}",
                    error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                    step='process_spamcheck'
                )
            except Exception as log_error:
                self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
            
            spamcheck.status = 'failed'
            await asyncio.to_thread(spamcheck.save)
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Launching scheduled Bison spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get scheduled spamchecks
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheckBison.objects.filter(
                Q(scheduled_at__lte=now) | Q(scheduled_at__isnull=True),
                status='pending'
            ).select_related('user', 'user_organization'))
        )

        if not spamchecks:
            self.stdout.write("No spamchecks scheduled")
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks to launch")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\nProcessed {len(spamchecks)} spamchecks:")
        self.stdout.write(f"- Launched: {success_count}")
        self.stdout.write(f"- Failed: {len(spamchecks) - success_count}")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 