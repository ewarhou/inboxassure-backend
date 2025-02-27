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
- send_bison_email: Sends email via Bison API with EmailGuard filter phrase

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
        url = f"{base_url}/api/sender-emails/{email_account}"
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
        """Send email via Bison API"""
        url = f"{base_url}/api/replies/new"
        headers = {
            "Authorization": f"Bearer {bison_api_key}",
            "Content-Type": "application/json"
        }
        
        # Debug log the test_emails structure
        self.stdout.write(f"Test emails data: {test_emails}")
        
        # Prepare all recipients in the format Bison API expects
        to_emails = [
            {
                "name": email.get('name', ''),
                "email_address": email['email']
            }
            for email in test_emails
        ]
        
        data = {
            "subject": spamcheck.subject,
            "message": f"{spamcheck.body}\n\n{filter_phrase}",
            "sender_email_id": bison_account_id,
            "content_type": "text" if spamcheck.plain_text else "html",
            "to_emails": to_emails
        }

        # Debug log the request payload
        self.stdout.write(f"Request payload: {json.dumps(data, indent=2)}")

        try:
            async with self.rate_limit:
                async with session.post(url, headers=headers, json=data) as response:
                    response_text = await response.text()
                    self.stdout.write(f"Response status: {response.status}")
                    self.stdout.write(f"Response body: {response_text}")
                    
                    if response.status == 200:
                        response_data = json.loads(response_text)
                        if response_data['data']['success']:
                            return True
                        else:
                            raise Exception(f"Bison API returned success=false: {response_text}")
                    elif response.status == 422:
                        self.stdout.write(self.style.ERROR(f"Validation error from Bison API: {response_text}"))
                        raise Exception(f"Validation error from Bison API: {response_text}")
                    else:
                        raise Exception(f"Failed to send email: {response.status} - {response_text}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error sending Bison email: {str(e)}"))
            raise

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck"""
        try:
            self.stdout.write(f"\nProcessing Bison spamcheck {spamcheck.id}")
            
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

            # Get accounts to check
            all_accounts = await asyncio.to_thread(
                lambda: list(spamcheck.accounts.all())
            )

            if not all_accounts:
                self.stdout.write("No accounts to check")
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
                    try:
                        # Find a connected account for this domain
                        connected_account = None
                        for account in domain_accounts:
                            account_info = await self.get_bison_account_id(
                                session, account.email_account, 
                                user_bison.bison_organization_api_key,
                                user_settings.bison_base_url
                            )
                            if account_info['is_connected']:
                                connected_account = account
                                bison_account_id = account_info['id']
                                break
                        
                        if not connected_account:
                            self.stdout.write(f"\nSkipping domain {domain} - No connected accounts found")
                            continue

                        self.stdout.write(f"\nProcessing domain {domain} with {len(domain_accounts)} accounts")
                        self.stdout.write(f"Representative account: {connected_account.email_account}")

                        # Get one EmailGuard tag for the domain
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
                            
                            # Get Bison account ID and check status for each account
                            account_info = await self.get_bison_account_id(
                                session, account.email_account,
                                user_bison.bison_organization_api_key,
                                user_settings.bison_base_url
                            )
                            
                            if account_info['is_connected']:
                                # Send email only for connected accounts
                                await self.send_bison_email(
                                    session, spamcheck, account, test_emails,
                                    user_bison.bison_organization_api_key,
                                    account_info['id'], filter_phrase,
                                    user_settings.bison_base_url
                                )
                                successful_accounts += 1
                            else:
                                self.stdout.write(f"Skipping disconnected account: {account.email_account}")
                            
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error processing domain {domain}: {str(e)}"))
                        
                        # Log the error
                        try:
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='processing_error',
                                provider='bison',
                                error_message=f"Error processing domain {domain}: {str(e)}",
                                error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                                step='process_domain'
                            )
                        except Exception as log_error:
                            self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                        
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
                            user_settings.bison_base_url
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
                        await self.send_bison_email(
                            session, spamcheck, account, test_emails,
                            user_bison.bison_organization_api_key,
                            account_info['id'], filter_phrase,
                            user_settings.bison_base_url
                        )

                        successful_accounts += 1

                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"Error processing account {account.email_account}: {str(e)}"))
                        
                        # Log the error
                        try:
                            await asyncio.to_thread(
                                SpamcheckErrorLog.objects.create,
                                user=spamcheck.user,
                                bison_spamcheck=spamcheck,
                                error_type='processing_error',
                                provider='bison',
                                error_message=f"Error processing account {account.email_account}: {str(e)}",
                                error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                                account_email=account.email_account,
                                step='process_account'
                            )
                        except Exception as log_error:
                            self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                        
                        spamcheck.status = 'failed'
                        await asyncio.to_thread(spamcheck.save)
                        return False

            # Only update to in_progress if at least one account was successful
            if successful_accounts > 0:
                spamcheck.status = 'in_progress'
                if spamcheck.recurring_days:
                    spamcheck.scheduled_at = timezone.now() + timedelta(days=spamcheck.recurring_days)
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