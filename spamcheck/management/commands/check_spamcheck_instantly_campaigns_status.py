from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from django.db.models import Count, Q
from settings.models import UserSettings, UserInstantly
from django.contrib.auth import get_user_model
import requests
import json
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Check Instantly campaign status and update spamcheck status accordingly'

    def check_campaign_status(self, campaign_id, api_key):
        """Check status of a campaign in Instantly"""
        url = f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

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

            all_completed = True
            for campaign in active_campaigns:
                self.stdout.write(f"\n----- Campaign {campaign.id} -----")
                self.stdout.write(f"Instantly Campaign ID: {campaign.instantly_campaign_id}")
                
                try:
                    # Get campaign status from Instantly
                    data = self.check_campaign_status(
                        campaign.instantly_campaign_id,
                        spamcheck.user_organization.instantly_api_key
                    )
                    
                    # Log full API response for debugging
                    self.stdout.write("\nAPI Response:")
                    self.stdout.write(json.dumps(data, indent=2))

                    # Extract campaign status
                    campaign_status = data.get('status')
                    self.stdout.write(f"\nStatus from API: {campaign_status} (type: {type(campaign_status)})")

                    # Get campaign statistics
                    stats = data.get('statistics', {})
                    total_leads = stats.get('total_leads', 0)
                    processed_leads = stats.get('processed_leads', 0)
                    remaining_leads = stats.get('remaining_leads', 0)
                    failed_leads = stats.get('failed_leads', 0)

                    self.stdout.write("\nCampaign Statistics:")
                    self.stdout.write(f"- Total leads: {total_leads}")
                    self.stdout.write(f"- Processed leads: {processed_leads}")
                    self.stdout.write(f"- Remaining leads: {remaining_leads}")
                    self.stdout.write(f"- Failed leads: {failed_leads}")

                    # Check if campaign is completed
                    is_completed = data.get('is_completed', False)
                    progress = data.get('progress', 0)
                    self.stdout.write(f"\nProgress: {progress}%")
                    self.stdout.write(f"Is Completed: {is_completed}")

                    # Determine if campaign should be marked as completed
                    should_complete = any([
                        campaign_status == 3,
                        campaign_status == "3",
                        is_completed is True,
                        progress == 100,
                        (processed_leads + failed_leads) == total_leads and total_leads > 0
                    ])

                    if should_complete:
                        self.stdout.write("\nMarking campaign as completed because:")
                        if campaign_status == 3 or campaign_status == "3":
                            self.stdout.write("- Status is 3")
                        if is_completed:
                            self.stdout.write("- is_completed flag is True")
                        if progress == 100:
                            self.stdout.write("- Progress is 100%")
                        if (processed_leads + failed_leads) == total_leads and total_leads > 0:
                            self.stdout.write("- All leads have been processed")

                        campaign.campaign_status = 'completed'
                        campaign.save()
                        self.stdout.write(self.style.SUCCESS("Campaign marked as completed"))
                    else:
                        self.stdout.write("\nCampaign is still active because:")
                        self.stdout.write(f"- Status is {campaign_status}")
                        self.stdout.write(f"- is_completed is {is_completed}")
                        self.stdout.write(f"- Progress is {progress}%")
                        self.stdout.write(f"- {processed_leads + failed_leads}/{total_leads} leads processed")
                        all_completed = False

                except requests.exceptions.RequestException as e:
                    self.stdout.write(self.style.ERROR(f"API request failed: {str(e)}"))
                    all_completed = False
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"Error processing campaign: {str(e)}"))
                    all_completed = False

            # Update spamcheck status if all campaigns are completed
            if all_completed and active_campaigns.exists():
                self.stdout.write("\nAll campaigns completed, updating spamcheck status...")
                spamcheck.status = 'generating_reports'
                spamcheck.save()
                self.stdout.write(self.style.SUCCESS(f"Updated spamcheck {spamcheck.id} to generating_reports"))

        self.stdout.write("\n=== Campaign Status Check Complete ===\n") 