from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns, UserSpamcheckReport
from settings.models import UserSettings
import aiohttp
import asyncio
from datetime import timedelta
import json

class Command(BaseCommand):
    help = 'Generate reports for spamchecks in generating_reports status'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second

    async def get_emailguard_report(self, session, emailguard_tag, emailguard_api_key):
        """Get report from EmailGuard API"""
        url = "https://n8n.findymail.app/webhook/emailguard"
        headers = {
            "Content-Type": "application/json"
        }
        data = {
            "URL": f"https://app.emailguard.io/api/v1/inbox-placement-tests/{emailguard_tag}",
            "Emailguard Token": emailguard_api_key
        }

        try:
            self.stdout.write(f"Making request to {url} with data: {data}")
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    raise Exception(f"EmailGuard API error: {response.status}")
                
                response_text = await response.text()
                self.stdout.write(f"Raw API Response: {response_text}")
                
                try:
                    response_data = json.loads(response_text)
                    if isinstance(response_data, str):
                        self.stdout.write(self.style.ERROR(f"API returned string instead of JSON: {response_data}"))
                        return None
                    return response_data
                except json.JSONDecodeError as e:
                    self.stdout.write(self.style.ERROR(f"Failed to parse JSON response: {str(e)}"))
                    return None
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error getting EmailGuard report: {str(e)}"))
            return None

    def calculate_score(self, results, provider):
        """Calculate score for a specific provider"""
        if not results or not isinstance(results, list):
            self.stdout.write(self.style.ERROR(f"Invalid results format. Expected list, got: {type(results)}"))
            return 0.0

        provider_results = [r for r in results if isinstance(r, dict) and r.get('provider') == provider]
        if not provider_results:
            self.stdout.write(f"No results found for provider: {provider}")
            return 0.0

        inbox_count = sum(1 for r in provider_results if isinstance(r, dict) and r.get('folder', '').lower() == 'inbox')
        total_count = len(provider_results)
        
        self.stdout.write(f"Provider {provider}:")
        self.stdout.write(f"- Inbox count: {inbox_count}")
        self.stdout.write(f"- Total count: {total_count}")
        
        # Calculate score as percentage (0-1) with 2 decimal places
        # Example: 8/8 = 1.00, 4/8 = 0.50, 6/12 = 0.50
        score = inbox_count / total_count if total_count > 0 else 0.0
        rounded_score = round(score, 2)
        
        self.stdout.write(f"- Score: {inbox_count}/{total_count} = {rounded_score}")
        return rounded_score

    async def process_campaign(self, session, campaign, user_settings):
        """Process a single campaign and generate report"""
        try:
            self.stdout.write(f"\nProcessing campaign {campaign.id} with tag {campaign.emailguard_tag}")
            
            # Get report from EmailGuard
            report_data = await self.get_emailguard_report(
                session,
                campaign.emailguard_tag,
                user_settings.emailguard_api_key
            )
            
            if not report_data:
                self.stdout.write(self.style.ERROR(f"No report data for campaign {campaign.id}"))
                return False

            # Check response structure
            if not isinstance(report_data, dict):
                self.stdout.write(self.style.ERROR(f"Invalid response format. Expected dict, got: {type(report_data)}"))
                return False

            # Get results from the correct path in response
            data = report_data.get('data', {})
            if not isinstance(data, dict):
                self.stdout.write(self.style.ERROR(f"Invalid data format. Expected dict, got: {type(data)}"))
                return False

            results = data.get('inbox_placement_test_emails', [])
            if not isinstance(results, list):
                self.stdout.write(self.style.ERROR(f"Invalid test emails format. Expected list, got: {type(results)}"))
                return False

            # Calculate scores
            google_score = self.calculate_score(results, 'Google')
            outlook_score = self.calculate_score(results, 'Microsoft')

            self.stdout.write(f"Scores calculated:")
            self.stdout.write(f"- Google Pro Score: {google_score}")
            self.stdout.write(f"- Outlook Pro Score: {outlook_score}")

            # Create report
            report = await asyncio.to_thread(
                UserSpamcheckReport.objects.create,
                organization=campaign.organization,
                email_account=campaign.account_id.email_account,
                report_link=f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}",
                google_pro_score=google_score,
                outlook_pro_score=outlook_score,
                spamcheck_instantly=campaign.spamcheck
            )

            self.stdout.write(self.style.SUCCESS(f"Created report {report.id} for campaign {campaign.id}"))
            return True

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing campaign {campaign.id}: {str(e)}"))
            self.stdout.write(self.style.ERROR(f"Full error details: {e.__class__.__name__}: {str(e)}"))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck and all its completed campaigns"""
        try:
            self.stdout.write(f"\nProcessing spamcheck {spamcheck.id}")
            
            # Get user settings
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )

            # Get all completed campaigns
            campaigns = await asyncio.to_thread(
                lambda: list(UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck,
                    campaign_status='completed'
                ).select_related('organization', 'account_id', 'spamcheck'))
            )

            if not campaigns:
                self.stdout.write(f"No completed campaigns found for spamcheck {spamcheck.id}")
                return False

            # Process all campaigns
            results = await asyncio.gather(*[
                self.process_campaign(session, campaign, user_settings)
                for campaign in campaigns
            ])

            # If all campaigns processed successfully, mark spamcheck as completed
            if all(results):
                await asyncio.to_thread(
                    lambda: setattr(spamcheck, 'status', 'completed') or spamcheck.save()
                )
                self.stdout.write(self.style.SUCCESS(f"Spamcheck {spamcheck.id} marked as completed"))
                return True
            else:
                self.stdout.write(self.style.ERROR(f"Some campaigns failed for spamcheck {spamcheck.id}"))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()
        two_hours_ago = now - timedelta(hours=2)

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Generating reports for spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get spamchecks that need report generation
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                status='generating_reports',
                updated_at__lte=two_hours_ago
            ).select_related('user'))
        )

        if not spamchecks:
            self.stdout.write(self.style.WARNING("No spamchecks need report generation"))
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks to process")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\nProcessed {len(spamchecks)} spamchecks:")
        self.stdout.write(f"- Successful: {success_count}")
        self.stdout.write(f"- Failed: {len(spamchecks) - success_count}")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 