"""
This command handles recurring spamchecks by:
1. Finding completed spamchecks with recurring_days set (both Instantly and Bison)
2. If completed for more than 1 hour:
   - Sets status back to pending
   - Updates scheduled_at to recurring_days after original schedule
3. Skips failed or paused spamchecks
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from spamcheck.models import UserSpamcheck, UserSpamcheckBison, SpamcheckErrorLog
from datetime import timedelta
import logging
import traceback

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Handle recurring spamchecks for both Instantly and Bison'

    def handle(self, *args, **options):
        """Entry point for the command"""
        now = timezone.now()
        one_hour_ago = now - timedelta(hours=1)

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Handling recurring spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Process Instantly spamchecks
        self.process_instantly_spamchecks(now, one_hour_ago)
        
        # Process Bison spamchecks
        self.process_bison_spamchecks(now, one_hour_ago)

        self.stdout.write("\nDone handling all recurring spamchecks")
    
    def process_instantly_spamchecks(self, now, one_hour_ago):
        """Process Instantly spamchecks with recurring settings"""
        self.stdout.write(f"\n{'-'*50}")
        self.stdout.write("Processing Instantly spamchecks")
        self.stdout.write(f"{'-'*50}\n")
        
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
            self.stdout.write("No recurring Instantly spamchecks to handle")
            return

        self.stdout.write(f"Found {spamchecks.count()} Instantly spamchecks to handle")

        for spamcheck in spamchecks:
            try:
                self.stdout.write(f"\nProcessing Instantly spamcheck {spamcheck.id}:")
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

                self.stdout.write(self.style.SUCCESS(f"✓ Updated Instantly spamcheck {spamcheck.id}"))

            except Exception as e:
                error_message = f"Error processing Instantly spamcheck {spamcheck.id}: {str(e)}"
                self.stdout.write(self.style.ERROR(error_message))
                
                # Log the error
                try:
                    SpamcheckErrorLog.objects.create(
                        user=spamcheck.user,
                        spamcheck=spamcheck,
                        error_type='recurring_error',
                        provider='system',
                        error_message=error_message,
                        error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                        step='process_recurring_instantly'
                    )
                    
                    # Update spamcheck status to failed
                    spamcheck.status = 'failed'
                    spamcheck.save()
                    
                except Exception as log_error:
                    self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                continue
    
    def process_bison_spamchecks(self, now, one_hour_ago):
        """Process Bison spamchecks with recurring settings"""
        self.stdout.write(f"\n{'-'*50}")
        self.stdout.write("Processing Bison spamchecks")
        self.stdout.write(f"{'-'*50}\n")
        
        # Find completed Bison spamchecks with recurring_days
        bison_spamchecks = UserSpamcheckBison.objects.filter(
            status='completed',
            recurring_days__isnull=False,
            recurring_days__gt=0,
            updated_at__lte=one_hour_ago  # Completed for more than 1 hour
        ).exclude(
            status__in=['failed', 'paused']  # Skip failed and paused
        )

        if not bison_spamchecks:
            self.stdout.write("No recurring Bison spamchecks to handle")
            return

        self.stdout.write(f"Found {bison_spamchecks.count()} Bison spamchecks to handle")

        for spamcheck in bison_spamchecks:
            try:
                self.stdout.write(f"\nProcessing Bison spamcheck {spamcheck.id}:")
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

                self.stdout.write(self.style.SUCCESS(f"✓ Updated Bison spamcheck {spamcheck.id}"))

            except Exception as e:
                error_message = f"Error processing Bison spamcheck {spamcheck.id}: {str(e)}"
                self.stdout.write(self.style.ERROR(error_message))
                
                # Log the error
                try:
                    SpamcheckErrorLog.objects.create(
                        user=spamcheck.user,
                        bison_spamcheck=spamcheck,
                        error_type='recurring_error',
                        provider='system',
                        error_message=error_message,
                        error_details={'full_error': str(e), 'traceback': traceback.format_exc()},
                        step='process_recurring_bison'
                    )
                    
                    # Update spamcheck status to failed
                    spamcheck.status = 'failed'
                    spamcheck.save()
                    
                except Exception as log_error:
                    self.stdout.write(self.style.ERROR(f"Error logging error: {str(log_error)}"))
                
                continue 