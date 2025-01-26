from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from django.db.models import Count, Q
from settings.models import UserSettings, UserInstantly
from django.contrib.auth import get_user_model
import requests
import json

class Command(BaseCommand):
    help = 'Check Instantly campaign status and update spamcheck status accordingly'

    def handle(self, *args, **options):
        """Entry point for the command"""
        self.stdout.write("\n=== Starting Campaign Status Check ===")
        self.stdout.write(f"Current time: {timezone.now()}")

        # Get all spamchecks with active or completed campaigns
        spamchecks = UserSpamcheck.objects.filter(
            status='in_progress'
        ).prefetch_related('campaigns', 'user_organization')

        if not spamchecks:
            self.stdout.write("No spamchecks to check")
            return

        for spamcheck in spamchecks:
            self.stdout.write(f"\n{'='*50}")
            self.stdout.write(f"Processing Spamcheck {spamcheck.id}:")
            self.stdout.write(f"Name: {spamcheck.name}")
            self.stdout.write(f"Organization: {spamcheck.user_organization.instantly_organization_name}")

            # Get user settings for API tokens
            try:
                user_settings = UserSettings.objects.get(user=spamcheck.user)
                if not user_settings.instantly_user_token:
                    self.stdout.write(self.style.ERROR("No Instantly user token found"))
                    continue
            except UserSettings.DoesNotExist:
                self.stdout.write(self.style.ERROR("User settings not found"))
                continue

            # Get active campaigns
            active_campaigns = spamcheck.campaigns.filter(campaign_status='active')
            self.stdout.write(f"\nFound {active_campaigns.count()} active campaigns")

            for campaign in active_campaigns:
                self.stdout.write(f"\n----- Campaign {campaign.id} -----")
                self.stdout.write(f"Instantly Campaign ID: {campaign.instantly_campaign_id}")
                
                # Call Instantly API to check campaign status
                url = f"https://api.instantly.ai/api/v2/campaigns/{campaign.instantly_campaign_id}"
                headers = {
                    "Authorization": f"Bearer {spamcheck.user_organization.instantly_api_key}",
                    "Content-Type": "application/json"
                }
                
                try:
                    self.stdout.write("\nCalling Instantly API...")
                    self.stdout.write(f"URL: {url}")
                    self.stdout.write(f"Headers: {headers}")

                    response = requests.get(url, headers=headers)
                    self.stdout.write(f"Response Status Code: {response.status_code}")
                    
                    if response.status_code == 200:
                        data = response.json()
                        self.stdout.write(f"Response Data: {json.dumps(data, indent=2)}")

                        # Check campaign status
                        campaign_status = data.get('status')
                        self.stdout.write(f"Campaign Status from API: {campaign_status}")

                        if campaign_status == 3:  # 3 means completed in Instantly
                            self.stdout.write("Campaign is completed, updating status...")
                            campaign.campaign_status = 'completed'
                            campaign.save()
                            self.stdout.write(self.style.SUCCESS("Campaign status updated to completed"))
                    else:
                        self.stdout.write(self.style.ERROR(f"API call failed: {response.text}"))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error checking campaign status: {str(e)}"))

            # After checking all campaigns, update spamcheck status if needed
            active_count = spamcheck.campaigns.filter(campaign_status='active').count()
            completed_count = spamcheck.campaigns.filter(campaign_status='completed').count()

            self.stdout.write(f"\nFinal Campaign Counts:")
            self.stdout.write(f"- Active campaigns: {active_count}")
            self.stdout.write(f"- Completed campaigns: {completed_count}")

            # Update spamcheck status to generating_reports if all campaigns are completed
            if active_count == 0 and completed_count > 0:
                self.stdout.write("All campaigns completed, updating spamcheck status...")
                spamcheck.status = 'generating_reports'
                spamcheck.save(update_fields=['status', 'updated_at'])
                self.stdout.write(self.style.SUCCESS(f"Updated spamcheck {spamcheck.id} to generating_reports status"))

        self.stdout.write("\n=== Campaign Status Check Complete ===\n") 