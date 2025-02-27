"""
LAUNCH SPAMCHECK SCRIPT
======================
Launches scheduled spamchecks by:
1. Finding pending spamchecks
2. For each spamcheck:
   - If domain-based is enabled:
     * Groups accounts by domain
     * Randomly selects one account per domain
   - If domain-based is disabled:
     * Uses all accounts as is
3. For each selected account:
   - Updates account sending limit to 100
   - Gets EmailGuard tag
   - Creates Instantly campaign
   - Adds test email addresses
   - Launches campaign
   - Stores campaign info

Key Functions:
- get_emailguard_tag: Creates test and returns UUID
- create_instantly_campaign: Sets up campaign with EmailGuard filter phrase in body
- add_leads: Adds test emails to campaign
- launch_campaign: Activates the campaign
- filter_domain_accounts: Groups accounts by domain and randomly selects one per domain

Critical Settings:
- Rate limit: 10 requests/second
- Campaign timezone: Etc/GMT+12
- Email gap: 1
- Campaign daily limit: 100
- Account sending limit: 100 (set before campaign creation)

Domain-based Features:
- Detects if spamcheck is domain-based
- Groups accounts by domain (@gmail.com, @outlook.com, etc.)
- Randomly selects one account per domain
- Only launches campaigns for selected accounts
- Other accounts with same domain get results in report phase

Runs via cron: * * * * * (every minute)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import (
    UserSpamcheck, 
    UserSpamcheckCampaigns,
    SpamcheckErrorLog
)
from settings.models import UserSettings, UserInstantly
import aiohttp
import asyncio
import json
import logging
from datetime import timedelta
import pytz
import requests
import random
from itertools import groupby
from operator import attrgetter
import traceback

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Launch scheduled spamchecks'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second

    def get_domain_from_email(self, email):
        """Extract domain from email address"""
        return email.split('@')[1] if '@' in email else None

    def filter_domain_accounts(self, accounts, is_domain_based):
        """Filter accounts based on domain settings
        If domain-based is enabled:
        - Groups accounts by domain
        - Randomly selects one account per domain
        If domain-based is disabled:
        - Returns all accounts as is
        """
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
                    if response.status in [200, 201]:  # Accept both 200 and 201 as success
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

    async def create_instantly_campaign(self, session, spamcheck, account, test_emails, instantly_api_key, emailguard_tag, filter_phrase):
        """Create campaign in Instantly"""
        url = "https://api.instantly.ai/api/v2/campaigns"
        headers = {
            "Authorization": f"Bearer {instantly_api_key}",
            "Content-Type": "application/json"
        }

        # Calculate schedule in campaign timezone
        campaign_tz = pytz.timezone('Etc/GMT+12')
        current_time = timezone.now().astimezone(campaign_tz)
        
        # Round to nearest 30 minutes
        minutes = current_time.minute
        if minutes < 30:
            start_minutes = "30"
            start_hour = str(current_time.hour).zfill(2)
        else:
            start_minutes = "00"
            start_hour = str((current_time.hour + 1) % 24).zfill(2)
            
        # End time is 1 hour after start
        end_hour = str((int(start_hour) + 1) % 24).zfill(2)

        data = {
            "name": f"{spamcheck.name} - {account.email_account}",
            "campaign_schedule": {
                "schedules": [{
                    "name": "Default Schedule",
                    "timing": {
                        "from": f"{start_hour}:{start_minutes}",
                        "to": f"{end_hour}:{start_minutes}"
                    },
                    "days": {str(i): True for i in range(7)},
                    "timezone": "Etc/GMT+12"
                }]
            },
            "email_gap": 1,
            "text_only": spamcheck.options.text_only,
            "email_list": [account.email_account],
            "daily_limit": 100,
            "stop_on_reply": True,
            "stop_on_auto_reply": True,
            "link_tracking": spamcheck.options.link_tracking,
            "open_tracking": spamcheck.options.open_tracking,
            "sequences": [{
                "steps": [{
                    "type": "email",
                    "variants": [{
                        "subject": spamcheck.options.subject,
                        "body": f"{spamcheck.options.body}\n\n{filter_phrase}"
                    }]
                }]
            }]
        }

        try:
            async with self.rate_limit:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status == 200:
                        campaign_data = await response.json()
                        return campaign_data['id']
                    else:
                        raise Exception(f"Instantly API error: {response.status}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error creating campaign: {str(e)}"))
            raise

    async def add_leads(self, session, campaign_id, test_emails, user_token, org_token):
        """Add test email addresses to campaign"""
        url = "https://app.instantly.ai/backend/api/v1/lead/add"
        headers = {
            "Cookie": f"__session={user_token}",
            "X-Org-Auth": org_token,
            "Content-Type": "application/json"
        }
        data = {
            "campaign_id": campaign_id,
            "skip_if_in_workspace": False,
            "skip_if_in_campaign": False,
            "leads": [{"email": email["email"]} for email in test_emails]
        }

        try:
            async with self.rate_limit:
                async with session.post(url, headers=headers, json=data) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to add leads: {response.status}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error adding leads: {str(e)}"))
            raise

    async def launch_campaign(self, session, campaign_id, instantly_api_key):
        """Launch campaign in Instantly"""
        url = f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate"
        headers = {
            "Authorization": f"Bearer {instantly_api_key}",
            "Content-Type": "application/json"
        }

        try:
            async with self.rate_limit:
                async with session.post(url, headers=headers, json={}) as response:
                    if response.status != 200:
                        raise Exception(f"Failed to launch campaign: {response.status}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error launching campaign: {str(e)}"))
            raise

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck"""
        try:
            self.stdout.write(f"\nProcessing spamcheck {spamcheck.id}")
            
            # Get required settings and tokens
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )
            
            user_instantly = await asyncio.to_thread(
                UserInstantly.objects.get,
                user=spamcheck.user,
                instantly_organization_id=spamcheck.user_organization.instantly_organization_id
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

            # Process each account
            for account in accounts:
                try:
                    # Update account sending limit
                    self.stdout.write(f"\nUpdating sending limit for account {account.email_account}")
                    update_limit_response = await asyncio.to_thread(
                        requests.post,
                        "https://app.instantly.ai/backend/api/v1/account/update/bulk",
                        headers={
                            "Cookie": f"__session={user_settings.instantly_user_token}",
                            "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                            "Content-Type": "application/json"
                        },
                        json={
                            "payload": {
                                "daily_limit": "100"
                            },
                            "emails": [account.email_account]
                        }
                    )
                    
                    if update_limit_response.status_code != 200:
                        self.stdout.write(self.style.WARNING(f"Failed to update account sending limit: {update_limit_response.text}"))
                    else:
                        self.stdout.write(f"✓ Account sending limit updated to 100")

                    # Get EmailGuard tag
                    campaign_name = f"{spamcheck.name} - {account.email_account}"
                    tag, test_emails, filter_phrase = await self.get_emailguard_tag(
                        session, campaign_name, user_settings.emailguard_api_key
                    )

                    # Create campaign
                    campaign_id = await self.create_instantly_campaign(
                        session, spamcheck, account, test_emails, user_instantly.instantly_api_key, tag, filter_phrase
                    )

                    # Add leads
                    await self.add_leads(
                        session, campaign_id, test_emails,
                        user_settings.instantly_user_token,
                        spamcheck.user_organization.instantly_organization_token
                    )

                    # Launch campaign
                    await self.launch_campaign(
                        session, campaign_id, user_instantly.instantly_api_key
                    )

                    # Store campaign info
                    await asyncio.to_thread(
                        UserSpamcheckCampaigns.objects.create,
                        user=spamcheck.user,
                        spamcheck=spamcheck,
                        organization=spamcheck.user_organization,
                        account_id=account,
                        instantly_campaign_id=campaign_id,
                        emailguard_tag=tag
                    )

                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error processing account {account.email_account}: {str(e)}"))
                    
                    # Log the error
                    try:
                        await asyncio.to_thread(
                            SpamcheckErrorLog.objects.create,
                            user=spamcheck.user,
                            spamcheck=spamcheck,
                            error_type='processing_error',
                            provider='instantly',
                            error_message=f"Error processing account {account.email_account}: {str(e)}",
                            error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                            account_email=account.email_account,
                            step='process_account'
                        )
                    except Exception as log_error:
                        self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                    
                    # Update spamcheck status to failed
                    spamcheck.status = 'failed'
                    await asyncio.to_thread(spamcheck.save)
                    return False

            # Only update to in_progress if all accounts were processed successfully
            spamcheck.status = 'in_progress'
            if spamcheck.recurring_days:
                spamcheck.scheduled_at = timezone.now() + timedelta(days=spamcheck.recurring_days)
            await asyncio.to_thread(spamcheck.save)

            return True

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            
            # Log the error
            try:
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    spamcheck=spamcheck,
                    error_type='processing_error',
                    provider='instantly',
                    error_message=f"Error processing spamcheck {spamcheck.id}: {str(e)}",
                    error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                    step='process_spamcheck'
                )
            except Exception as log_error:
                self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
            
            # Update spamcheck status to failed
            spamcheck.status = 'failed'
            await asyncio.to_thread(spamcheck.save)
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Launching scheduled spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get scheduled spamchecks
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                Q(scheduled_at__lte=now) | Q(scheduled_at__isnull=True),
                status='pending'
            ).select_related('user', 'user_organization', 'options'))
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