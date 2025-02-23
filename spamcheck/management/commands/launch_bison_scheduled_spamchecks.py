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
from spamcheck.models import UserSpamcheckBison, UserSpamcheckAccountsBison, UserSpamcheckReport
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

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Launch scheduled Bison spamchecks'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second

    def get_domain_from_email(self, email):
        """Extract domain from email address"""
        return email.split('@')[1] if '@' in email else None

    def filter_domain_accounts(self, accounts, is_domain_based):
        """Filter accounts based on domain settings"""
        if not is_domain_based:
            return accounts

        # Group accounts by domain
        accounts_by_domain = {}
        for account in accounts:
            domain = self.get_domain_from_email(account.email_account)
            if domain:
                if domain not in accounts_by_domain:
                    accounts_by_domain[domain] = []
                accounts_by_domain[domain].append(account)

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
        """Get Bison account ID for email account"""
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
                        return data['data']['id']
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

            # Filter accounts based on domain settings
            accounts = self.filter_domain_accounts(all_accounts, spamcheck.is_domain_based)
            
            if spamcheck.is_domain_based:
                self.stdout.write(f"\nDomain-based spamcheck enabled:")
                self.stdout.write(f"- Total accounts: {len(all_accounts)}")
                self.stdout.write(f"- Selected accounts (one per domain): {len(accounts)}")
                for account in accounts:
                    domain = self.get_domain_from_email(account.email_account)
                    self.stdout.write(f"  * {domain}: {account.email_account}")

            # Track successful accounts
            successful_accounts = 0
            total_accounts = len(accounts)

            # Process each account
            for account in accounts:
                try:
                    # Get Bison account ID first
                    bison_account_id = await self.get_bison_account_id(
                        session, account.email_account, user_bison.bison_organization_api_key,
                        user_settings.bison_base_url
                    )

                    # Get EmailGuard tag once for this account
                    campaign_name = f"{spamcheck.name} - {account.email_account}"
                    tag, test_emails, filter_phrase = await self.get_emailguard_tag(
                        session, campaign_name, user_settings.emailguard_api_key
                    )

                    # Store EmailGuard tag in account with explicit transaction
                    self.stdout.write(f"Updating EmailGuard tag for account {account.email_account}")
                    self.stdout.write(f"New tag to be saved: {tag}")
                    
                    # Update tag in database directly
                    await asyncio.to_thread(
                        lambda: UserSpamcheckAccountsBison.objects.filter(id=account.id).update(
                            last_emailguard_tag=tag,
                            updated_at=timezone.now()
                        )
                    )
                    
                    # Refresh account instance from database
                    await asyncio.to_thread(account.refresh_from_db)
                    self.stdout.write(f"Tag after update: {account.last_emailguard_tag}")

                    # Send one email to all test addresses using the same tag
                    await self.send_bison_email(
                        session, spamcheck, account, test_emails,
                        user_bison.bison_organization_api_key,
                        bison_account_id, filter_phrase,
                        user_settings.bison_base_url
                    )

                    # Increment successful accounts counter
                    successful_accounts += 1

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error processing account {account.email_account}: {str(e)}"))
                    # Don't continue, set spamcheck to failed
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False

            # Only update to in_progress if all accounts were successful
            if successful_accounts == total_accounts:
                spamcheck.status = 'in_progress'
                if spamcheck.recurring_days:
                    spamcheck.scheduled_at = timezone.now() + timedelta(days=spamcheck.recurring_days)
                await asyncio.to_thread(spamcheck.save)
                return True
            else:
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            # Update spamcheck status to failed
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