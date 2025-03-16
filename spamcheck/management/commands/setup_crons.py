from django.core.management.base import BaseCommand
from crontab import CronTab
import os
import argparse

class Command(BaseCommand):
    help = 'Setup cron jobs for InboxAssure'

    def add_arguments(self, parser):
        parser.add_argument('--action', type=str, choices=['setup', 'enable', 'disable'], default='setup',
                          help='Action to perform: setup, enable, or disable jobs')
        parser.add_argument('--job', type=str, help='Specific job to enable/disable. Leave empty for all jobs')

    def handle(self, *args, **options):
        # Initialize crontab for the current user
        cron = CronTab(user=True)
        
        # Base directory for the project
        base_dir = "/var/www/inboxassure-backend"
        python_path = f"{base_dir}/venv/bin/python"
        manage_py = f"{base_dir}/manage.py"
        log_dir = "/var/log/inboxassure"

        # Define jobs with their schedules
        jobs = [
            {
                'command': 'handle_recurring_spamchecks',
                'schedule': '0 * * * *',  # Every hour
                'log': 'cron_recurring.log',
                'comment': 'inboxassure_recurring'  # Used to identify jobs
            },
            {
                'command': 'process_spamcheck_queue',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_queue.log',
                'comment': 'inboxassure_queue'
            },
            {
                'command': 'launch_instantly_scheduled_spamchecks',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_launch.log',
                'comment': 'inboxassure_launch'
            },
            {
                'command': 'generate_spamcheck_instantly_reports',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_reports.log',
                'comment': 'inboxassure_reports'
            },
            {
                'command': 'update_active_accounts',
                'schedule': '0 * * * *',  # Every hour
                'log': 'cron_accounts.log',
                'comment': 'inboxassure_accounts'
            },
            {
                'command': 'launch_bison_scheduled_spamchecks',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_bison_launch.log',
                'comment': 'inboxassure_bison_launch'
            },
            {
                'command': 'generate_spamcheck_bison_reports',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_bison_reports.log',
                'comment': 'inboxassure_bison_reports'
            }
        ]

        if options['action'] == 'setup':
            # Clear existing cron jobs
            cron.remove_all()

            # Ensure log directory exists
            os.makedirs(log_dir, exist_ok=True)

            # Create jobs
            for job in jobs:
                cmd = f"{python_path} {manage_py} {job['command']} >> {log_dir}/{job['log']} 2>&1"
                cron_job = cron.new(command=cmd, comment=job['comment'])
                cron_job.setall(job['schedule'])
                self.stdout.write(f"Created job: {job['command']}")

        elif options['action'] in ['enable', 'disable']:
            target_job = options['job']
            enable = options['action'] == 'enable'

            for job in cron:
                if not job.comment or not job.comment.startswith('inboxassure_'):
                    continue

                if target_job:
                    # Enable/disable specific job
                    matching_job = next((j for j in jobs if j['command'] == target_job), None)
                    if matching_job and job.comment == matching_job['comment']:
                        job.enable(enable)
                        status = "enabled" if enable else "disabled"
                        self.stdout.write(f"{status.capitalize()} job: {target_job}")
                        break
                else:
                    # Enable/disable all jobs
                    job.enable(enable)
                    status = "enabled" if enable else "disabled"
                    self.stdout.write(f"{status.capitalize()} job with comment: {job.comment}")

        # Write the crontab
        cron.write()
        
        self.stdout.write(self.style.SUCCESS('Operation completed successfully'))

    def list_jobs(self):
        """Helper method to list all jobs"""
        cron = CronTab(user=True)
        for job in cron:
            if job.comment and job.comment.startswith('inboxassure_'):
                status = "enabled" if job.is_enabled() else "disabled"
                self.stdout.write(f"Job: {job.command} | Status: {status} | Schedule: {job.slices}") 