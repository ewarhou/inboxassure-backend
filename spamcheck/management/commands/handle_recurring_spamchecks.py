"""
This command handles recurring spamchecks by:
1. Finding completed spamchecks with recurring_days set (both Instantly and Bison)
2. Immediately:
   - Sets status back to pending
   - Updates scheduled_at to recurring_days after original schedule
   - If weekdays are specified, adjusts to the next available day in the weekdays list
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

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Handling recurring spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Process Instantly spamchecks
        self.process_instantly_spamchecks(now)
        
        # Process Bison spamchecks
        self.process_bison_spamchecks(now)

        self.stdout.write("\nDone handling all recurring spamchecks")
    
    def get_next_schedule_date(self, base_date, recurring_days, weekdays=None):
        """
        Calculate the next scheduled date, prioritizing weekdays if set.
        Always sets the time to 00:30 AM regardless of original time.
        
        Args:
            base_date: The original scheduled date or current time
            recurring_days: Number of days to recur
            weekdays: List of weekdays (0=Monday, 6=Sunday) or None
            
        Returns:
            Next scheduled date with time set to 00:30 AM
        """
        now = timezone.now()
        
        # If base_date is significantly in the past (more than 1 day), use current time instead
        if base_date < (now - timedelta(days=1)):
            self.stdout.write(f"  - Original scheduled date is over 1 day in the past ({base_date})")
            self.stdout.write(f"  - Using current time instead: {now}")
            base_date = now
        
        # First calculate the initial next date by adding recurring days
        next_date = base_date + timedelta(days=recurring_days)
        
        # Ensure the next date is in the future
        if next_date <= now:
            self.stdout.write(f"  - Calculated next date {next_date} is not in the future")
            # Add recurring days to current time to get a future date
            days_to_add = recurring_days
            next_date = now + timedelta(days=days_to_add)
            self.stdout.write(f"  - Adjusted to future date: {next_date}")
        
        # If weekdays is not set or empty, just process the calculated date
        if not weekdays:
            # Only adjust the time part to 00:30 while keeping the date part
            next_date = next_date.replace(hour=0, minute=30, second=0, microsecond=0)
            return next_date
        
        # Convert weekdays to integers if they're strings
        weekday_ints = []
        for day in weekdays:
            try:
                weekday_ints.append(int(day))
            except (ValueError, TypeError):
                # Skip if not convertible to int
                continue
        
        # If no valid weekdays after conversion, return the calculated date
        if not weekday_ints:
            # Only adjust the time part to 00:30 while keeping the date part
            next_date = next_date.replace(hour=0, minute=30, second=0, microsecond=0)
            return next_date
        
        # Get the weekday of the calculated date (0=Monday, 6=Sunday)
        calculated_weekday = next_date.weekday()
        
        # If the calculated date falls on one of the specified weekdays, return it
        if calculated_weekday in weekday_ints:
            # Only adjust the time part to 00:30 while keeping the date part
            next_date = next_date.replace(hour=0, minute=30, second=0, microsecond=0)
            return next_date
        
        # Find the next date that falls on one of the specified weekdays
        days_checked = 0
        max_days_to_check = 7  # Check up to a week ahead
        
        while days_checked < max_days_to_check:
            next_date += timedelta(days=1)
            days_checked += 1
            
            if next_date.weekday() in weekday_ints:
                # Only adjust the time part to 00:30 while keeping the date part
                next_date = next_date.replace(hour=0, minute=30, second=0, microsecond=0)
                return next_date
        
        # If no suitable day found within a week, return the closest day
        # Find the day with the minimum distance from the list
        min_distance = 7
        closest_day = 0
        
        for day in weekday_ints:
            distance = (day - calculated_weekday) % 7
            if distance < min_distance:
                min_distance = distance
                closest_day = day
        
        # Adjust the date to the closest weekday
        days_to_add = (closest_day - calculated_weekday) % 7
        result_date = next_date + timedelta(days=days_to_add - days_checked)
        
        # Set the time to 00:30
        result_date = result_date.replace(hour=0, minute=30, second=0, microsecond=0)
        
        return result_date
    
    def process_instantly_spamchecks(self, now):
        """Process Instantly spamchecks with recurring settings"""
        self.stdout.write(f"\n{'-'*50}")
        self.stdout.write("Processing Instantly spamchecks")
        self.stdout.write(f"{'-'*50}\n")
        
        # Find completed spamchecks with recurring_days (process immediately)
        spamchecks = UserSpamcheck.objects.filter(
            status='completed',
            recurring_days__isnull=False,
            recurring_days__gt=0
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

                if hasattr(spamcheck, 'weekdays') and spamcheck.weekdays:
                    self.stdout.write(f"- Weekdays: {spamcheck.weekdays}")

                # Use the base date (original schedule or now)
                base_date = spamcheck.scheduled_at if spamcheck.scheduled_at else now
                
                # Calculate next schedule with weekday consideration
                weekdays = getattr(spamcheck, 'weekdays', None)
                next_schedule = self.get_next_schedule_date(
                    base_date, 
                    spamcheck.recurring_days,
                    weekdays
                )

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
    
    def process_bison_spamchecks(self, now):
        """Process Bison spamchecks with recurring settings"""
        self.stdout.write(f"\n{'-'*50}")
        self.stdout.write("Processing Bison spamchecks")
        self.stdout.write(f"{'-'*50}\n")
        
        # Find completed Bison spamchecks with recurring_days (process immediately)
        bison_spamchecks = UserSpamcheckBison.objects.filter(
            status='completed',
            recurring_days__isnull=False,
            recurring_days__gt=0
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

                if hasattr(spamcheck, 'weekdays') and spamcheck.weekdays:
                    self.stdout.write(f"- Weekdays: {spamcheck.weekdays}")

                # Use the base date (original schedule or now)
                base_date = spamcheck.scheduled_at if spamcheck.scheduled_at else now
                
                # Calculate next schedule with weekday consideration
                weekdays = getattr(spamcheck, 'weekdays', None)
                next_schedule = self.get_next_schedule_date(
                    base_date, 
                    spamcheck.recurring_days,
                    weekdays
                )

                self.stdout.write(f"- Next schedule: {next_schedule}")

                # Update spamcheck
                spamcheck.status = 'queued'
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