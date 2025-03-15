"""
PROCESS SPAMCHECK QUEUE SCRIPT
============================
Processes queued spamchecks one by one per user:
1. Finds all users with queued spamchecks
2. For each user, selects their oldest queued spamcheck
3. Changes its status from 'queued' to 'pending'
4. The existing launcher will then pick it up

This ensures:
- Only one spamcheck per user is processed at a time
- No user can monopolize system resources
- Fair distribution of processing capacity

Runs via cron: * * * * * (every minute)
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from django.contrib.auth import get_user_model
from spamcheck.models import UserSpamcheckBison
from settings.api import log_to_terminal
import logging

logger = logging.getLogger(__name__)
User = get_user_model()

class Command(BaseCommand):
    help = 'Process queued spamchecks one by one per user'

    def handle(self, *args, **options):
        now = timezone.now()
        
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Processing spamcheck queue at {now}")
        self.stdout.write(f"{'='*50}\n")
        
        # Get all users with queued spamchecks
        users_with_queued = User.objects.filter(
            bison_spamchecks__status='queued'
        ).distinct()
        
        if not users_with_queued:
            self.stdout.write("No users with queued spamchecks found")
            return
            
        self.stdout.write(f"Found {users_with_queued.count()} users with queued spamchecks")
        
        # Track how many spamchecks were processed
        processed_count = 0
        
        for user in users_with_queued:
            # Check if user already has a spamcheck in progress
            in_progress_count = UserSpamcheckBison.objects.filter(
                user=user,
                status__in=['pending', 'in_progress', 'generating_reports']
            ).count()
            
            if in_progress_count > 0:
                self.stdout.write(f"User {user.email} already has {in_progress_count} spamcheck(s) in progress. Skipping.")
                continue
                
            # Get the oldest queued spamcheck for this user
            next_spamcheck = UserSpamcheckBison.objects.filter(
                user=user,
                status='queued'
            ).order_by('created_at').first()
            
            if next_spamcheck:
                # Change status to 'pending' so the existing launcher can pick it up
                next_spamcheck.status = 'pending'
                next_spamcheck.save()
                
                log_to_terminal("SpamcheckQueue", "Process", f"Moved spamcheck {next_spamcheck.id} ({next_spamcheck.name}) to pending for user {user.email}")
                self.stdout.write(f"Moved spamcheck {next_spamcheck.id} ({next_spamcheck.name}) to pending for user {user.email}")
                processed_count += 1
        
        self.stdout.write(f"\nQueue processing complete. Moved {processed_count} spamchecks to pending status.") 