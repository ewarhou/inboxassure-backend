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
                                
                                # Log this specific failure
                                await asyncio.to_thread(
                                    SpamcheckErrorLog.objects.create,
                                    user=spamcheck.user,
                                    bison_spamcheck=spamcheck,
                                    error_type='api_error',
                                    provider='bison',
                                    error_message=f"Bison API returned success=false for {email['email']}",
                                    error_details={'response': response_text},
                                    account_email=account.email_account,
                                    step='send_bison_email',
                                    api_endpoint=url,
                                    status_code=response.status,
                                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                                )
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
                                status_code=response.status,
                                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                            )
                        elif response.status == 422:
                            failed_sends += 1
                            self.stdout.write(self.style.ERROR(f"Validation error from Bison API for {email['email']}: {response_text}"))
                            
                            # Log validation error
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='validation_error',
                                provider='bison',
                                error_message=f"Validation error for {email['email']}",
                                error_details={'response': response_text},
                                account_email=account.email_account,
                                step='send_bison_email',
                                api_endpoint=url,
                                status_code=response.status,
                                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                            )
                        else:
                            failed_sends += 1
                            self.stdout.write(self.style.ERROR(f"Failed to send email to {email['email']}: {response.status} - {response_text}"))
                            
                            # Log general API error
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='api_error',
                                provider='bison',
                                error_message=f"Failed to send email to {email['email']}: HTTP {response.status}",
                                error_details={'response': response_text},
                                account_email=account.email_account,
                                step='send_bison_email',
                                api_endpoint=url,
                                status_code=response.status,
                                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                            )
            except Exception as e:
                failed_sends += 1
                self.stdout.write(self.style.ERROR(f"Error sending Bison email to {email['email']}: {str(e)}"))
                
                # Log exception
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='exception',
                    provider='bison',
                    error_message=f"Exception sending email to {email['email']}",
                    error_details={'error': str(e), 'traceback': traceback.format_exc()},
                    account_email=account.email_account,
                    step='send_bison_email',
                    api_endpoint=url,
                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                )
        
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
            
            # NEW: Refresh accounts if needed (tag-based or all accounts)
            if spamcheck.account_selection_type in ['tag_based', 'all']:
                self.stdout.write(f"Account selection type: {spamcheck.account_selection_type}")
                refresh_success = await self.refresh_accounts(session, spamcheck, user_bison)
                if not refresh_success:
                    self.stdout.write(self.style.ERROR(f"Failed to refresh accounts for {spamcheck.account_selection_type} selection"))
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
            
            # NEW: Fetch campaign copy if campaign_copy_source_id is set
            if spamcheck.campaign_copy_source_id:
                await self.fetch_campaign_copy(session, spamcheck, user_bison)
            
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
                                    step='process_account',
                                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
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
                                step='process_domain',
                                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
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
                
                # Only fail the spamcheck if we have 30 or more server errors or all domains failed
                if self.server_error_count >= 30000:
                    self.stdout.write(self.style.ERROR(f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed."))
                    
                    # Log the failure due to too many server errors
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='too_many_server_errors',
                        provider='bison',
                        error_message=f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed.",
                        step='process_domain_based',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                    )
                    
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
                
                if len(failed_domains) == len(accounts_by_domain):
                    self.stdout.write(self.style.ERROR(f"All domains failed. Marking spamcheck as failed."))
                    
                    # Log the failure due to all domains failing
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='all_domains_failed',
                        provider='bison',
                        error_message=f"All domains ({len(failed_domains)}) failed. Marking spamcheck as failed.",
                        error_details={'failed_domains': list(failed_domains)},
                        step='process_domain_based',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                    )
                    
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
                                step='process_account',
                                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
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
                        
                # Only fail the spamcheck if we have 30 or more server errors or all accounts failed
                if self.server_error_count >= 30000:
                    self.stdout.write(self.style.ERROR(f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed."))
                    
                    # Log the failure due to too many server errors
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='too_many_server_errors',
                        provider='bison',
                        error_message=f"Too many Bison Server Errors ({self.server_error_count}). Marking spamcheck as failed.",
                        step='process_non_domain_based',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                    )
                    
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False
                
                if len(failed_accounts) == total_accounts:
                    self.stdout.write(self.style.ERROR(f"All accounts failed. Marking spamcheck as failed."))
                    
                    # Log the failure due to all accounts failing
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='all_accounts_failed',
                        provider='bison',
                        error_message=f"All accounts ({len(failed_accounts)}) failed. Marking spamcheck as failed.",
                        error_details={'failed_accounts_count': len(failed_accounts)},
                        step='process_non_domain_based',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                    )
                    
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False

            # Only update to in_progress if at least one account was successful
            if successful_accounts > 0:
                spamcheck.status = 'waiting_for_reports'
                await asyncio.to_thread(spamcheck.save)
                return True
            else:
                error_msg = f"No accounts were successfully processed for spamcheck {spamcheck.id}"
                self.stdout.write(self.style.ERROR(error_msg))
                
                # Log the error
                try:
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='no_successful_processing',
                        provider='bison',
                        error_message=error_msg,
                        error_details={
                            'total_accounts': len(all_accounts),
                            'failed_accounts': len(failed_accounts),
                            'server_error_count': self.server_error_count
                        },
                        step='process_accounts',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
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
                    step='process_spamcheck',
                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
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

        # Create a TCP connector with a custom DNS resolver to help with DNS resolution issues
        tcp_connector = aiohttp.TCPConnector(
            family=0,  # Allow both IPv4 and IPv6
            ssl=True,  # Properly verify SSL certificates
            use_dns_cache=True,  # Enable DNS caching
            ttl_dns_cache=300,  # Cache DNS results for 5 minutes
            limit=100  # Allow more connections
        )

        # Process all spamchecks
        async with aiohttp.ClientSession(connector=tcp_connector) as session:
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

    async def refresh_accounts(self, session, spamcheck, user_bison):
        """
        Refresh accounts for the spamcheck based on selection type.
        - If specific accounts, keep existing accounts
        - If all accounts, fetch all accounts from Bison API
        - If tag-based, fetch accounts from Bison API with matching tags
        """
        self.stdout.write(f"Refreshing accounts for spamcheck {spamcheck.id}")
        
        # If using specific accounts, no need to refresh
        if spamcheck.account_selection_type == 'specific':
            self.stdout.write("Using specific accounts, no refresh needed")
            return True
            
        # Get URL with base URL cleanup to handle potential variations
        base_url = user_bison.base_url.rstrip('/')
        # Check for API hostname DNS resolution issues and provide fallback
        hostname = base_url.split('://')[1].split('/')[0]
        self.stdout.write(f"Using API hostname: {hostname}")
        
        # If hostname is 'send.savedby.io', warn about potential DNS issues
        if hostname == 'send.savedby.io':
            self.stdout.write(self.style.WARNING("Note: Using send.savedby.io which may have DNS resolution issues"))
            
        # Fetch accounts from Bison API
        url = f"{base_url}/api/sender-emails"
        headers = {
            "Authorization": f"Bearer {user_bison.bison_organization_api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            # Initialize variables for pagination
            all_accounts = []
            page = 1
            has_more = True
            
            self.stdout.write(f"Fetching accounts from Bison API for {spamcheck.account_selection_type} selection")
            
            while has_more:
                params = {"page": page}
                
                try:
                    async with self.rate_limit:
                        async with session.get(url, headers=headers, params=params) as response:
                            if response.status != 200:
                                error_text = await response.text()
                                error_msg = f"Failed to fetch accounts: {response.status} - {error_text}"
                                self.stdout.write(self.style.ERROR(error_msg))
                                
                                # Log API error
                                await asyncio.to_thread(
                                    SpamcheckErrorLog.objects.create,
                                    user=spamcheck.user,
                                    bison_spamcheck=spamcheck,
                                    error_type='api_error',
                                    provider='bison',
                                    error_message=error_msg,
                                    error_details={'response': error_text},
                                    step='refresh_accounts',
                                    api_endpoint=url,
                                    status_code=response.status,
                                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                                )
                                
                                raise Exception(error_msg)
                            
                            data = await response.json()
                            current_page_accounts = data.get("data", [])
                            
                            # Check if we have more pages
                            has_more = (
                                data and 
                                data.get("data") and 
                                isinstance(data.get("data"), list) and 
                                len(data.get("data")) > 0
                            )
                            
                            if current_page_accounts:
                                all_accounts.extend(current_page_accounts)
                                self.stdout.write(f"Found {len(current_page_accounts)} accounts on page {page}")
                                page += 1
                            else:
                                self.stdout.write(f"No more accounts found on page {page}")
                                break
                except aiohttp.client_exceptions.ClientConnectorError as dns_error:
                    # Special handling for DNS errors
                    if "No address associated with hostname" in str(dns_error):
                        error_msg = f"DNS resolution error: Cannot resolve host {hostname}. This is likely a DNS configuration issue on the server."
                        self.stdout.write(self.style.ERROR(error_msg))
                        self.stdout.write(self.style.WARNING(f"Recommended solution: Update DNS configuration or add an entry to /etc/hosts for {hostname}"))
                        
                        # Log the detailed error for debugging
                        await asyncio.to_thread(
                            SpamcheckErrorLog.objects.create,
                            user=spamcheck.user,
                            bison_spamcheck=spamcheck,
                            error_type='dns_error',
                            provider='bison',
                            error_message=error_msg,
                            error_details={
                                'hostname': hostname,
                                'full_error': str(dns_error),
                                'traceback': traceback.format_exc()
                            },
                            step='refresh_accounts',
                            workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                        )
                        raise Exception(error_msg)
                    else:
                        # Re-raise other connection errors
                        raise
            
            self.stdout.write(f"Total accounts fetched: {len(all_accounts)}")
            
            if not all_accounts:
                error_msg = "No accounts found in Bison organization"
                self.stdout.write(self.style.ERROR(error_msg))
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='no_accounts',
                    provider='bison',
                    error_message=error_msg,
                    step='refresh_accounts',
                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                )
                
                return False
            
            # Filter accounts based on selection type
            filtered_accounts = []
            
            if spamcheck.account_selection_type == 'all':
                # Use all active accounts
                filtered_accounts = [
                    account for account in all_accounts 
                    if account.get("status") == "Connected"
                ]
                self.stdout.write(f"Selected all connected accounts: {len(filtered_accounts)}")
                
            elif spamcheck.account_selection_type == 'tag_based':
                # Filter by tags
                include_tags = spamcheck.include_tags or []
                exclude_tags = spamcheck.exclude_tags or []
                
                for account in all_accounts:
                    # Skip if account is not active
                    if account.get("status") != "Connected":
                        continue
                        
                    # Get account tags
                    account_tags = [tag.get("name", "").lower() for tag in account.get("tags", [])] if account.get("tags") else []
                    
                    # Skip if has any excluded tag
                    if exclude_tags and account_tags:
                        should_skip = False
                        for exclude_tag in exclude_tags:
                            exclude_tag_lower = exclude_tag.lower()
                            if any(exclude_tag_lower == tag.lower() for tag in account_tags):
                                should_skip = True
                                break
                        if should_skip:
                            continue
                    
                    # Skip if doesn't have any of the required tags
                    if include_tags and account_tags:
                        has_required_tag = False
                        for include_tag in include_tags:
                            include_tag_lower = include_tag.lower()
                            if any(include_tag_lower == tag.lower() for tag in account_tags):
                                has_required_tag = True
                                break
                        if not has_required_tag:
                            continue
                    
                    filtered_accounts.append(account)
                
                self.stdout.write(f"Selected {len(filtered_accounts)} accounts matching tag criteria")
            
            if not filtered_accounts:
                error_msg = f"No accounts were selected after filtering for {spamcheck.account_selection_type} selection"
                self.stdout.write(self.style.WARNING(error_msg))
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='no_matching_accounts',
                    provider='bison',
                    error_message=error_msg,
                    error_details={
                        'selection_type': spamcheck.account_selection_type,
                        'include_tags': spamcheck.include_tags,
                        'exclude_tags': spamcheck.exclude_tags,
                        'total_accounts': len(all_accounts)
                    },
                    step='refresh_accounts',
                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                )
                
                return False
                
            # Clear existing accounts first
            await asyncio.to_thread(
                lambda: UserSpamcheckAccountsBison.objects.filter(
                    bison_spamcheck=spamcheck
                ).delete()
            )
            self.stdout.write(f"Cleared existing accounts for spamcheck {spamcheck.id}")
            
            # Create new account entries
            accounts_to_create = []
            for account in filtered_accounts:
                email = account.get("email")
                if not email:
                    continue
                    
                accounts_to_create.append(
                    UserSpamcheckAccountsBison(
                        user=spamcheck.user,
                        organization=user_bison,
                        bison_spamcheck=spamcheck,
                        email_account=email
                    )
                )
            
            # Bulk create the accounts
            if accounts_to_create:
                await asyncio.to_thread(
                    lambda: UserSpamcheckAccountsBison.objects.bulk_create(accounts_to_create)
                )
                self.stdout.write(f"Created {len(accounts_to_create)} new account entries")
                return True
            else:
                error_msg = "No valid accounts to create after filtering"
                self.stdout.write(self.style.WARNING(error_msg))
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='no_valid_accounts',
                    provider='bison',
                    error_message=error_msg,
                    step='refresh_accounts',
                    workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                )
                
                return False
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error refreshing accounts: {str(e)}"))
            
            # Log the error
            await asyncio.to_thread(
                SpamcheckErrorLog.objects.create,
                user=spamcheck.user,
                bison_spamcheck=spamcheck,
                error_type='processing_error',
                provider='bison',
                error_message=f"Error refreshing accounts: {str(e)}",
                error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                step='refresh_accounts',
                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
            )
            return False
    
    async def fetch_campaign_copy(self, session, spamcheck, user_bison):
        """
        Fetch email subject and body from campaign if campaign_copy_source_id is set
        """
        if not spamcheck.campaign_copy_source_id:
            self.stdout.write(f"No campaign copy source ID set for spamcheck {spamcheck.id}")
            return True
            
        self.stdout.write(f"Fetching campaign copy from campaign ID: {spamcheck.campaign_copy_source_id}")
        
        # Get URL with base URL cleanup to handle potential variations
        base_url = user_bison.base_url.rstrip('/')
        # Check for API hostname DNS resolution issues
        hostname = base_url.split('://')[1].split('/')[0]
        
        url = f"{base_url}/api/campaigns/{spamcheck.campaign_copy_source_id}/sequence-steps"
        headers = {
            "Authorization": f"Bearer {user_bison.bison_organization_api_key}",
            "Content-Type": "application/json"
        }
        
        try:
            try:
                async with self.rate_limit:
                    async with session.get(url, headers=headers) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            self.stdout.write(self.style.WARNING(f"Could not fetch campaign copy: {response.status} - {error_text}"))
                            self.stdout.write(f"Using existing subject and body from spamcheck")
                            return True
                        
                        data = await response.json()
                        steps = data.get('data', [])
                        
                        if not steps:
                            self.stdout.write(self.style.WARNING(f"No sequence steps found for campaign {spamcheck.campaign_copy_source_id}"))
                            self.stdout.write(f"Using existing subject and body from spamcheck")
                            return True
                        
                        # Get the first step (step 1)
                        first_step = steps[0]
                        subject = first_step.get('email_subject', '')
                        html_body = first_step.get('email_body', '')
                        
                        if not subject or not html_body:
                            self.stdout.write(self.style.WARNING(f"Campaign has empty subject or body"))
                            self.stdout.write(f"Using existing subject and body from spamcheck")
                            return True
                        
                        # Convert HTML to plain text
                        try:
                            import re
                            from html import unescape
                            
                            # Function to convert HTML to formatted plain text
                            def html_to_text(html):
                                if not html:
                                    return ""
                                
                                # Unescape HTML entities
                                html = unescape(html)
                                
                                # Replace common block elements with newlines
                                html = re.sub(r'</(div|p|h\d|ul|ol|li|blockquote|pre|table|tr)>', '\n', html)
                                
                                # Replace <br> tags with newlines
                                html = re.sub(r'<br[^>]*>', '\n', html)
                                
                                # Replace multiple consecutive newlines with just two
                                html = re.sub(r'\n{3,}', '\n\n', html)
                                
                                # Remove all remaining HTML tags
                                html = re.sub(r'<[^>]*>', '', html)
                                
                                # Trim leading/trailing whitespace
                                html = html.strip()
                                
                                return html
                            
                            plain_body = html_to_text(html_body)
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"Error converting HTML to text: {str(e)}"))
                            plain_body = html_body  # Fallback to original HTML
                        
                        # Update the spamcheck with the new subject and body
                        await asyncio.to_thread(
                            lambda: UserSpamcheckBison.objects.filter(id=spamcheck.id).update(
                                subject=subject,
                                body=plain_body
                            )
                        )
                        
                        # Refresh the spamcheck object
                        spamcheck.subject = subject
                        spamcheck.body = plain_body
                        
                        self.stdout.write(f"Updated spamcheck with campaign copy:")
                        self.stdout.write(f"Subject: {subject}")
                        self.stdout.write(f"Body: {plain_body[:100]}...")
                        return True
            except aiohttp.client_exceptions.ClientConnectorError as dns_error:
                # Special handling for DNS errors
                if "No address associated with hostname" in str(dns_error):
                    error_msg = f"DNS resolution error: Cannot resolve host {hostname}. This is likely a DNS configuration issue on the server."
                    self.stdout.write(self.style.ERROR(error_msg))
                    self.stdout.write(self.style.WARNING(f"Recommended solution: Update DNS configuration or add an entry to /etc/hosts for {hostname}"))
                    
                    # Log the detailed error for debugging
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='dns_error',
                        provider='bison',
                        error_message=error_msg,
                        error_details={
                            'hostname': hostname,
                            'full_error': str(dns_error),
                            'traceback': traceback.format_exc()
                        },
                        step='fetch_campaign_copy',
                        workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
                    )
                    
                    # Continue with existing copy
                    self.stdout.write(self.style.WARNING("Using existing subject and body due to DNS error"))
                    return True
                else:
                    # Re-raise other connection errors
                    raise
                    
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching campaign copy: {str(e)}"))
            
            # Log the error
            await asyncio.to_thread(
                SpamcheckErrorLog.objects.create,
                user=spamcheck.user,
                bison_spamcheck=spamcheck,
                error_type='processing_error',
                provider='bison',
                error_message=f"Error fetching campaign copy: {str(e)}",
                error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                step='fetch_campaign_copy',
                workspace_id=str(spamcheck.user_organization_id) if spamcheck.user_organization_id else None
            )
            return True  # Continue with existing copy 