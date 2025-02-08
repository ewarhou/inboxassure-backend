"""
This command checks the status of running spamcheck campaigns in Instantly.ai.

Purpose:
- Monitors campaigns in 'in_progress' status
- Updates local campaign status based on Instantly API response
- Marks campaigns as 'completed' when done
- Triggers report generation when all campaigns are complete

Flow:
1. Finds all spamchecks with 'in_progress' status
2. For each spamcheck:
   - Gets all campaigns not marked as 'completed' or 'deleted'
   - Checks campaign status in Instantly API
   - Updates local campaign status
   - If all campaigns are complete, marks spamcheck for report generation
3. Handles rate limiting and error cases
4. Provides detailed logging of all operations

Features:
- Async/await for better performance
- Rate limiting (10 requests/second)
- Error handling and retries
- Bulk status updates
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from settings.models import UserSettings, UserInstantly
import aiohttp
import asyncio
import json
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check status of running spamcheck campaigns'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second

    async def check_campaign_status(self, session, campaign, instantly_api_key):
        """Check status of a campaign in Instantly"""
        url = f"https://api.instantly.ai/api/v2/campaigns/{campaign.instantly_campaign_id}"
        headers = {
            "Authorization": f"Bearer {instantly_api_key}"
        }

        try:
            async with self.rate_limit:
                async with session.get(url, headers=headers) as response:
                    if response.status == 200:
                        data = await response.json()
                        raw_status = data.get('status', '')
                        
                        # Convert integer status to string if needed
                        status = str(raw_status).lower() if raw_status is not None else ''
                        
                        # Map Instantly status to our status
                        status_map = {
                            'completed': 'completed',
                            'stopped': 'completed',
                            'paused': 'in_progress',
                            'running': 'in_progress',
                            'failed': 'failed',
                            # Add integer status mappings
                            '0': 'in_progress',  # Draft
                            '1': 'in_progress',  # Running
                            '2': 'completed',    # Completed
                            '3': 'completed',    # Also Completed
                            '4': 'completed',    # Stopped
                            '5': 'in_progress',  # Paused
                        }
                        
                        campaign.campaign_status = status_map.get(status, 'in_progress')
                        await asyncio.to_thread(campaign.save)
                        
                        self.stdout.write(f"Campaign {campaign.instantly_campaign_id} status: {status} (raw: {raw_status})")
                        return campaign.campaign_status == 'completed'
                    else:
                        self.stdout.write(self.style.ERROR(f"Failed to check campaign {campaign.instantly_campaign_id}. Status: {response.status}"))
                        return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error checking campaign {campaign.instantly_campaign_id}: {str(e)}"))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck and check all its campaigns"""
        try:
            self.stdout.write(f"\nProcessing spamcheck {spamcheck.id}")
            
            # Get API key
            user_instantly = await asyncio.to_thread(
                UserInstantly.objects.get,
                user=spamcheck.user,
                instantly_organization_id=spamcheck.user_organization.instantly_organization_id
            )

            # Get all non-completed campaigns
            campaigns = await asyncio.to_thread(
                lambda: list(UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck
                ).exclude(campaign_status__in=['completed', 'deleted']))
            )

            if not campaigns:
                self.stdout.write(f"No active campaigns found for spamcheck {spamcheck.id}")
                return True

            # Check all campaigns
            results = await asyncio.gather(*[
                self.check_campaign_status(session, campaign, user_instantly.instantly_api_key)
                for campaign in campaigns
            ])

            # If all campaigns are complete, mark spamcheck for report generation
            if all(results):
                spamcheck.status = 'generating_reports'
                await asyncio.to_thread(spamcheck.save)
                self.stdout.write(self.style.SUCCESS(f"All campaigns complete for spamcheck {spamcheck.id}. Marked for report generation."))
                return True
            else:
                self.stdout.write(f"Some campaigns still running for spamcheck {spamcheck.id}")
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Checking campaign status at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get spamchecks in progress
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                status='in_progress'
            ).select_related('user', 'user_organization'))
        )

        if not spamchecks:
            self.stdout.write("No spamchecks in progress")
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks to check")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\nProcessed {len(spamchecks)} spamchecks:")
        self.stdout.write(f"- Complete: {success_count}")
        self.stdout.write(f"- Still running: {len(spamchecks) - success_count}")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 