from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from django.db.models import Count, Q

class Command(BaseCommand):
    help = 'Check status of in-progress spamchecks and update if completed'

    def handle(self, *args, **options):
        """Entry point for the command"""
        now = timezone.now()
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Checking in-progress spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # First check all spamchecks
        all_spamchecks = UserSpamcheck.objects.all()
        self.stdout.write(f"\nTotal spamchecks in database: {all_spamchecks.count()}")
        for spamcheck in all_spamchecks:
            self.stdout.write(f"- Spamcheck {spamcheck.id}: status='{spamcheck.status}'")

        # Check specifically in_progress ones
        in_progress_spamchecks = UserSpamcheck.objects.filter(status='in_progress')
        self.stdout.write(f"\nSpamchecks with status='in_progress': {in_progress_spamchecks.count()}")

        # For each in_progress spamcheck, check if all its campaigns are completed
        spamchecks_to_update = []
        for spamcheck in in_progress_spamchecks:
            total_campaigns = UserSpamcheckCampaigns.objects.filter(spamcheck=spamcheck).count()
            active_campaigns = UserSpamcheckCampaigns.objects.filter(
                spamcheck=spamcheck,
                campaign_status='active'
            ).count()
            
            self.stdout.write(f"\nSpamcheck {spamcheck.id}:")
            self.stdout.write(f"- Total campaigns: {total_campaigns}")
            self.stdout.write(f"- Active campaigns: {active_campaigns}")
            
            if active_campaigns == 0 and total_campaigns > 0:
                spamchecks_to_update.append(spamcheck.id)
                self.stdout.write(f"✓ All campaigns completed for spamcheck {spamcheck.id}")

        if not spamchecks_to_update:
            self.stdout.write(self.style.WARNING("\nNo spamchecks to update - all have active campaigns"))
            return

        # Update status to generating_reports
        updated_count = UserSpamcheck.objects.filter(
            id__in=spamchecks_to_update
        ).update(status='generating_reports')

        self.stdout.write(self.style.SUCCESS(
            f"\nUpdated {updated_count} spamchecks to generating_reports status"
        ))

        # Print final campaign statuses for updated spamchecks
        for spamcheck_id in spamchecks_to_update:
            spamcheck = UserSpamcheck.objects.get(id=spamcheck_id)
            self.stdout.write(f"\nFinal status for spamcheck {spamcheck.id}:")
            campaigns = UserSpamcheckCampaigns.objects.filter(spamcheck=spamcheck)
            for campaign in campaigns:
                self.stdout.write(
                    f"- Campaign {campaign.instantly_campaign_id}: {campaign.campaign_status}"
                ) 