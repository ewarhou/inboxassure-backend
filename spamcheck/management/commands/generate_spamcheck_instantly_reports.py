"""
This command generates reports for completed spamcheck campaigns.

Purpose:
- Finds spamchecks in 'generating_reports' status
- Gets report data from EmailGuard API
- Creates/updates reports in database
- Updates account sending limits based on results
- Handles domain-based spamchecks

Flow:
1. Finds spamchecks ready for report generation
2. For each spamcheck:
   - Gets EmailGuard report data
   - Calculates scores
   - Creates/updates reports
   - Updates sending limits
   - Handles domain-based reporting
3. Marks spamchecks as completed when done

Features:
- Async/await for better performance
- Rate limiting
- Error handling
- Domain-based reporting
- Bulk operations
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns, UserSpamcheckReport
from settings.models import UserSettings
import aiohttp
import asyncio
from datetime import timedelta
import json
from collections import defaultdict
import requests
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Generate reports for completed spamcheck campaigns'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)
        self.conditions_met = False

    def calculate_score(self, data, provider):
        """Calculate provider score from EmailGuard data"""
        if not data or 'data' not in data or 'inbox_placement_test_emails' not in data['data']:
            return 0.0

        emails = data['data']['inbox_placement_test_emails']
        provider_emails = [e for e in emails if e['provider'] == provider]
        
        if not provider_emails:
            return 0.0

        # Count emails in inbox, safely handling None values
        inbox_count = sum(1 for e in provider_emails if e.get('folder') and e.get('folder', '').lower() == 'inbox')
        
        self.stdout.write(f"\nCalculating score for {provider}:")
        self.stdout.write(f"  Total {provider} emails: {len(provider_emails)}")
        self.stdout.write(f"  Emails in inbox: {inbox_count}")
        
        score = inbox_count / len(provider_emails)
        self.stdout.write(f"  Score: {score} ({inbox_count}/{len(provider_emails)})")
        
        return score

    def parse_conditions(self, conditions_str):
        """Parse conditions string into dict"""
        if not conditions_str:
            return {'google': 0.5, 'outlook': 0.5, 'daily_limit': 25}

        conditions = {}
        parts = conditions_str.lower().split('and')
        
        for part in parts:
            if 'google>=' in part:
                conditions['google'] = float(part.split('>=')[1])
            elif 'outlook>=' in part:
                conditions['outlook'] = float(part.split('>=')[1])
            elif 'sending=' in part:
                sending_parts = part.split('=')[1].split('/')
                conditions['daily_limit'] = int(sending_parts[0])

        return conditions

    def evaluate_conditions(self, spamcheck, google_score, outlook_score, email):
        """Evaluate if scores meet conditions"""
        conditions = self.parse_conditions(spamcheck.conditions)
        
        # Check if scores meet thresholds
        google_ok = google_score >= conditions.get('google', 0.5)
        outlook_ok = outlook_score >= conditions.get('outlook', 0.5)
        
        self.conditions_met = google_ok and outlook_ok
        
        if self.conditions_met:
            # Update sending limit
            daily_limit = conditions.get('daily_limit', 25)
            self.update_sending_limit(spamcheck.user_organization, email, daily_limit)
            
        return self.conditions_met

    def evaluate_default_condition(self, google_score, outlook_score):
        """Evaluate default condition (google >= 0.5)"""
        return google_score >= 0.5

    def update_sending_limit(self, organization, emails, daily_limit):
        """Update sending limit for email accounts"""
        if not isinstance(emails, list):
            emails = [emails]
            
        headers = {
            "Cookie": f"__session={organization.instantly_user_token}",
            "X-Org-Auth": organization.instantly_organization_token,
            "Content-Type": "application/json"
        }
        
        data = {
            "payload": {
                "daily_limit": str(daily_limit)
            },
            "emails": emails
        }
        
        try:
            response = requests.post(
                "https://app.instantly.ai/backend/api/v1/account/update/bulk",
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                self.stdout.write(f"  ✓ Updated sending limit to {daily_limit} for {len(emails)} account(s)")
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed to update sending limit: {response.text}"))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Error updating sending limit: {str(e)}"))

    async def process_campaign(self, session, campaign, user_settings):
        """Process a single campaign and generate report"""
        try:
            self.stdout.write(f"\n  Campaign ID: {campaign.instantly_campaign_id}")
            self.stdout.write(f"  EmailGuard Tag: {campaign.emailguard_tag}")
            
            # Get EmailGuard report
            url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{campaign.emailguard_tag}"
            headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}"
            }
            
            self.stdout.write("  Calling EmailGuard API...")
            response = requests.get(url, headers=headers)
            
            if response.status_code == 200:
                self.stdout.write("  API call successful")
                data = response.json()
                self.stdout.write(f"  Raw EmailGuard Response: {json.dumps(data, indent=2)}")

                # Calculate scores
                google_score = self.calculate_score(data, 'Google')
                outlook_score = self.calculate_score(data, 'Microsoft')

                self.stdout.write(f"  Google Score: {google_score}")
                self.stdout.write(f"  Outlook Score: {outlook_score}")

                # Create or update report
                report, created = UserSpamcheckReport.objects.update_or_create(
                    spamcheck_instantly=campaign.spamcheck,
                    email_account=campaign.account_id.email_account,
                    defaults={
                        'organization': campaign.organization,
                        'google_pro_score': google_score,
                        'outlook_pro_score': outlook_score,
                        'report_link': f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}",
                        'is_good': self.evaluate_conditions(campaign.spamcheck, google_score, outlook_score, campaign.account_id.email_account) if campaign.spamcheck.conditions else self.evaluate_default_condition(google_score, outlook_score)
                    }
                )

                self.stdout.write(f"  Report {'created' if created else 'updated'} successfully")
                self.stdout.write(f"  Account status: {'✓ Good' if report.is_good else '✗ Bad'}")

                # Evaluate conditions and update sending limits
                if campaign.spamcheck.conditions:
                    self.evaluate_conditions(campaign.spamcheck, google_score, outlook_score, campaign.account_id.email_account)
                else:
                    if google_score >= 0.5:
                        self.update_sending_limit(campaign.organization, campaign.account_id.email_account, 25)

                return True
            else:
                self.stdout.write(self.style.ERROR(f"  API call failed with status {response.status_code}"))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error processing campaign: {str(e)}"))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck"""
        try:
            self.stdout.write(f"\nProcessing Spamcheck ID: {spamcheck.id}")
            self.stdout.write(f"Last updated: {spamcheck.updated_at}")
            
            # Check if enough time has passed
            time_passed = timezone.now() - spamcheck.updated_at
            hours_passed = time_passed.total_seconds() / 3600
            waiting_time = spamcheck.reports_waiting_time or 1.0
            
            self.stdout.write(f"Waiting time configured: {waiting_time} hours")
            self.stdout.write(f"Time since last update: {hours_passed:.2f} hours")
            
            if hours_passed < waiting_time:
                self.stdout.write("Not enough time has passed. Skipping...")
                return False
                
            self.stdout.write("Enough time has passed. Processing campaigns...")

            # Get user settings
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )

            # Get all campaigns
            campaigns = await asyncio.to_thread(
                lambda: list(UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck
                ).select_related('account_id'))
            )

            if not campaigns:
                self.stdout.write("No campaigns found")
                return False

            # Process all campaigns
            results = await asyncio.gather(*[
                self.process_campaign(session, campaign, user_settings)
                for campaign in campaigns
            ])

            # Only mark as completed if all campaigns were processed successfully
            if all(results):
                spamcheck.status = 'completed'
                await asyncio.to_thread(spamcheck.save)
                self.stdout.write(self.style.SUCCESS(f"All reports generated successfully. Spamcheck {spamcheck.id} marked as completed"))
                return True
            else:
                self.stdout.write(self.style.WARNING(f"Some reports failed to generate for spamcheck {spamcheck.id}. Status remains as generating_reports"))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n=== Starting Report Generation ===")
        self.stdout.write(f"Current time: {now}")
        
        self.stdout.write("\nAPI Endpoints being used:")
        self.stdout.write("1. EmailGuard Report: GET https://app.emailguard.io/api/v1/inbox-placement-tests/{tag}")
        self.stdout.write("2. Instantly Update Account: POST https://app.instantly.ai/backend/api/v1/account/update/bulk")
        self.stdout.write("3. Instantly Delete Campaign: DELETE https://api.instantly.ai/api/v2/campaigns/{id}")
        self.stdout.write("---\n")

        # Get spamchecks ready for report generation
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                status='generating_reports'
            ).select_related('user', 'user_organization'))
        )

        if not spamchecks:
            self.stdout.write("No spamchecks in generating_reports status")
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks in generating_reports status\n")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\n=== Report Generation Complete ===")
        self.stdout.write(f"Successfully processed {success_count} of {len(spamchecks)} spamchecks")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options))