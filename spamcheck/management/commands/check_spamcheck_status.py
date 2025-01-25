from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from django.db.models import Count, Q
from settings.models import UserSettings, UserInstantly
from django.contrib.auth import get_user_model
import aiohttp
import asyncio
import time
from asyncio import Semaphore

class Command(BaseCommand):
    help = 'Check status of in-progress spamchecks and update if completed'
    
    def __init__(self):
        super().__init__()
        # Rate limit: 10 requests per second
        self.rate_limit = Semaphore(10)
        self.last_request_time = time.time()

    async def wait_for_rate_limit(self):
        """Ensure we don't exceed 10 requests per second"""
        async with self.rate_limit:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < 0.1:  # Less than 1/10th of a second
                await asyncio.sleep(0.1 - time_since_last)
            self.last_request_time = time.time()

    async def check_instantly_campaigns(self, session, organization, user_settings):
        """Check campaign statuses from Instantly API and update our database"""
        await self.wait_for_rate_limit()
        
        url = "https://app.instantly.ai/backend/api/v1/campaign/list"
        headers = {
            "Cookie": f"__session={user_settings.instantly_user_token}",
            "X-Org-Auth": organization.instantly_organization_token,
            "Content-Type": "application/json"
        }
        
        try:
            async with session.post(url, headers=headers, json={}) as response:
                data = await response.json()
                self.stdout.write(f"\nAPI Response for org {organization.id}:")
                self.stdout.write(f"Status Code: {response.status}")
                
                if isinstance(data, list):
                    campaigns = data
                elif isinstance(data, dict) and 'campaigns' in data:
                    campaigns = data['campaigns']
                else:
                    self.stdout.write(self.style.WARNING(f"Invalid response format for org {organization.id}"))
                    return

                # Get all our campaigns for this organization
                our_campaigns = await asyncio.to_thread(
                    lambda: list(UserSpamcheckCampaigns.objects.filter(
                        organization_id=organization.id
                    ).values('instantly_campaign_id', 'campaign_status', 'spamcheck_id'))
                )
                
                our_campaign_ids = [c['instantly_campaign_id'] for c in our_campaigns]
                
                self.stdout.write(f"Processing organization {organization.id}:")
                self.stdout.write(f"Total campaigns from API: {len(campaigns)}")
                self.stdout.write(f"Our campaigns in DB: {len(our_campaigns)}")
                
                # Process completed campaigns from API
                completed_api_campaigns = [
                    c for c in campaigns 
                    if c.get('status') == 3
                ]
                
                update_tasks = []
                for api_campaign in completed_api_campaigns:
                    api_id = api_campaign['id']
                    if api_id in our_campaign_ids:
                        our_campaign = next(
                            c for c in our_campaigns 
                            if c['instantly_campaign_id'] == api_id
                        )
                        
                        if our_campaign['campaign_status'] == 'active':
                            update_tasks.append(
                                asyncio.create_task(
                                    self.update_campaign_status(api_id)
                                )
                            )
                
                if update_tasks:
                    await asyncio.gather(*update_tasks)
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error checking Instantly campaigns for org {organization.id}: {str(e)}"))

    async def update_campaign_status(self, campaign_id):
        """Update a single campaign's status"""
        try:
            updated = await asyncio.to_thread(
                lambda: UserSpamcheckCampaigns.objects.filter(
                    instantly_campaign_id=campaign_id,
                    campaign_status='active'
                ).update(campaign_status='completed')
            )
            if updated:
                self.stdout.write(f"✓ Updated campaign {campaign_id} to completed")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error updating campaign {campaign_id}: {str(e)}"))

    async def process_organization(self, session, org_id, user_id):
        """Process a single organization"""
        try:
            user_settings = await asyncio.to_thread(
                lambda: UserSettings.objects.get(user_id=user_id)
            )
            organization = await asyncio.to_thread(
                lambda: UserInstantly.objects.get(id=org_id)
            )
            await self.check_instantly_campaigns(session, organization, user_settings)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing organization {org_id}: {str(e)}"))

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Checking in-progress spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        async with aiohttp.ClientSession() as session:
            # Get unique organizations from in_progress spamchecks
            org_ids = await asyncio.to_thread(
                lambda: list(UserSpamcheck.objects.filter(
                    status='in_progress'
                ).values_list('user_organization', 'user').distinct())
            )
            
            self.stdout.write(f"\nFound {len(org_ids)} organizations with in-progress spamchecks:")
            for org_id, user_id in org_ids:
                self.stdout.write(f"- Organization ID: {org_id}, User ID: {user_id}")
            
            # Process organizations in parallel
            tasks = [
                self.process_organization(session, org_id, user_id)
                for org_id, user_id in org_ids
            ]
            await asyncio.gather(*tasks)

        # Now check spamchecks in our database
        all_spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.all())
        )
        self.stdout.write(f"\nTotal spamchecks in database: {len(all_spamchecks)}")

        in_progress_spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(status='in_progress'))
        )
        self.stdout.write(f"Total in-progress spamchecks: {len(in_progress_spamchecks)}")

        # Process spamchecks in parallel
        async def check_spamcheck(spamcheck):
            active_campaigns = await asyncio.to_thread(
                lambda: list(UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck,
                    campaign_status='active'
                ).values('id', 'instantly_campaign_id'))
            )
            
            completed_campaigns = await asyncio.to_thread(
                lambda: UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck,
                    campaign_status='completed'
                ).count()
            )
            
            self.stdout.write(f"\nSpamcheck {spamcheck.id}:")
            self.stdout.write(f"- Active campaigns: {len(active_campaigns)}")
            self.stdout.write(f"- Completed campaigns: {completed_campaigns}")
            
            for campaign in active_campaigns:
                self.stdout.write(
                    f"- Active Campaign ID: {campaign['id']}, "
                    f"UUID: {campaign['instantly_campaign_id']}"
                )
            
            if not active_campaigns and completed_campaigns > 0:
                return spamcheck.id
            return None

        # Process all spamchecks in parallel
        spamcheck_results = await asyncio.gather(*[
            check_spamcheck(spamcheck) for spamcheck in in_progress_spamchecks
        ])
        spamchecks_to_update = [id for id in spamcheck_results if id is not None]

        if not spamchecks_to_update:
            self.stdout.write(self.style.WARNING(
                "\nNo spamchecks to update - all either have active campaigns or no completed campaigns"
            ))
            return

        # Update status to generating_reports
        updated_count = await asyncio.to_thread(
            lambda: UserSpamcheck.objects.filter(
                id__in=spamchecks_to_update
            ).update(status='generating_reports')
        )

        self.stdout.write(self.style.SUCCESS(
            f"\nUpdated {updated_count} spamchecks to generating_reports status"
        ))

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 