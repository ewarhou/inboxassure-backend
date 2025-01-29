"""
This command handles recurring spamchecks by:
1. Finding completed spamchecks with recurring_days set
2. If completed for more than 1 hour:
   - Sets status back to pending
   - Updates scheduled_at to recurring_days after original schedule
3. Skips failed or paused spamchecks
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Handle recurring spamchecks'

    def handle(self, *args, **options):
        """Entry point for the command"""
        now = timezone.now()
        one_hour_ago = now - timedelta(hours=1)

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Handling recurring spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Find completed spamchecks with recurring_days
        spamchecks = UserSpamcheck.objects.filter(
            status='completed',
            recurring_days__isnull=False,
            recurring_days__gt=0,
            updated_at__lte=one_hour_ago  # Completed for more than 1 hour
        ).exclude(
            status__in=['failed', 'paused']  # Skip failed and paused
        )

        if not spamchecks:
            self.stdout.write("No recurring spamchecks to handle")
            return

        self.stdout.write(f"Found {spamchecks.count()} spamchecks to handle")

        for spamcheck in spamchecks:
            try:
                self.stdout.write(f"\nProcessing spamcheck {spamcheck.id}:")
                self.stdout.write(f"- Name: {spamcheck.name}")
                self.stdout.write(f"- Original schedule: {spamcheck.scheduled_at}")
                self.stdout.write(f"- Recurring days: {spamcheck.recurring_days}")

                # Calculate next schedule based on original schedule
                if spamcheck.scheduled_at:
                    next_schedule = spamcheck.scheduled_at + timedelta(days=spamcheck.recurring_days)
                else:
                    # If no original schedule, use current time as base
                    next_schedule = now + timedelta(days=spamcheck.recurring_days)

                self.stdout.write(f"- Next schedule: {next_schedule}")

                # Update spamcheck
                spamcheck.status = 'pending'
                spamcheck.scheduled_at = next_schedule
                spamcheck.save()

                self.stdout.write(self.style.SUCCESS(f"✓ Updated spamcheck {spamcheck.id}"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
                continue

        self.stdout.write("\nDone handling recurring spamchecks") 