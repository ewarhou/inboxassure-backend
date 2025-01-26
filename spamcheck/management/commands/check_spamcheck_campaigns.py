from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns

class Command(BaseCommand):
    help = 'Check spamcheck campaigns status and update spamcheck status accordingly'

    def handle(self, *args, **options):
        """Entry point for the command"""
        # Get all spamchecks with active or completed campaigns
        spamchecks = UserSpamcheck.objects.filter(
            status='in_progress'
        ).prefetch_related('campaigns')

        if not spamchecks:
            self.stdout.write("No spamchecks to check")
            return

        updated_count = 0
        for spamcheck in spamchecks:
            # Count active and completed campaigns
            active_count = spamcheck.campaigns.filter(campaign_status='active').count()
            completed_count = spamcheck.campaigns.filter(campaign_status='completed').count()

            self.stdout.write(f"\nSpamcheck {spamcheck.id}:")
            self.stdout.write(f"- Active campaigns: {active_count}")
            self.stdout.write(f"- Completed campaigns: {completed_count}")

            # Update spamcheck status to generating_reports if all campaigns are completed
            if active_count == 0 and completed_count > 0:
                # Instead of using update(), get and save each spamcheck to trigger the save() method
                spamcheck.status = 'generating_reports'
                spamcheck.save(update_fields=['status', 'updated_at'])  # Force update of both fields
                updated_count += 1
                self.stdout.write(f"Updated spamcheck {spamcheck.id} to generating_reports status")

                # Print active campaign details if any
                active_campaigns = spamcheck.campaigns.filter(campaign_status='active')
                for campaign in active_campaigns:
                    self.stdout.write(f"- Active Campaign ID: {campaign.id}, UUID: {campaign.emailguard_tag}")

        self.stdout.write(f"\nUpdated {updated_count} spamchecks to generating_reports status") 