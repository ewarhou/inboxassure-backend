"""
InboxAssure Cron Job Setup Script

This script sets up, enables, disables, or lists cron jobs for InboxAssure in both development and production environments.

Usage:
    # Setup cron jobs for production environment
    python manage.py setup_crons --env prod
    
    # Setup cron jobs for development environment
    python manage.py setup_crons --env dev
    
    # List all configured cron jobs
    python manage.py setup_crons --action list
    
    # List cron jobs for a specific environment
    python manage.py setup_crons --action list --env prod
    
    # Enable a specific job in production
    python manage.py setup_crons --action enable --job process_spamcheck_queue --env prod
    
    # Disable a specific job in development
    python manage.py setup_crons --action disable --job update_active_accounts --env dev

Notes:
    - This script requires the python-crontab package
    - It must be run on the server where you want to set up the cron jobs
    - It will automatically detect the environment if --env is not specified
    - For production, it uses /var/www/inboxassure-production
    - For development, it uses /var/www/inboxassure-backend
"""

from django.core.management.base import BaseCommand
from crontab import CronTab
import os
import argparse
import socket

class Command(BaseCommand):
    help = 'Setup cron jobs for InboxAssure'

    def add_arguments(self, parser):
        parser.add_argument('--action', type=str, choices=['setup', 'enable', 'disable', 'list'], default='setup',
                          help='Action to perform: setup, enable, disable, or list jobs')
        parser.add_argument('--job', type=str, help='Specific job to enable/disable. Leave empty for all jobs')
        parser.add_argument('--env', type=str, choices=['dev', 'prod'], help='Environment: dev or prod. Defaults to auto-detect')

    def handle(self, *args, **options):
        # Initialize crontab for the current user
        cron = CronTab(user=True)
        
        # Determine environment based on hostname or argument
        if options.get('env'):
            env = options['env']
        else:
            # Auto-detect environment based on hostname
            hostname = socket.gethostname()
            # You may need to adjust this logic based on your actual hostnames
            env = 'prod' if '157.230.233.108' in hostname or 'inboxassure-production' in hostname else 'dev'
        
        # Set base directory based on environment
        if env == 'prod':
            base_dir = "/var/www/inboxassure-production"
        else:
            base_dir = "/var/www/inboxassure-backend"
            
        self.stdout.write(f"Setting up crons for {env} environment at {base_dir}")
        
        python_path = f"{base_dir}/venv/bin/python"
        manage_py = f"{base_dir}/manage.py"
        log_dir = "/var/log/inboxassure"

        # Define jobs with their schedules
        jobs = [
            {
                'command': 'handle_recurring_spamchecks',
                'schedule': '0 * * * *',  # Every hour
                'log': 'cron_recurring.log',
                'comment': f'inboxassure_{env}_recurring'  # Include env in comment
            },
            {
                'command': 'process_spamcheck_queue',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_queue.log',
                'comment': f'inboxassure_{env}_queue'
            },
            {
                'command': 'launch_instantly_scheduled_spamchecks',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_launch.log',
                'comment': f'inboxassure_{env}_launch'
            },
            {
                'command': 'generate_spamcheck_instantly_reports',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_reports.log',
                'comment': f'inboxassure_{env}_reports'
            },
            {
                'command': 'update_active_accounts',
                'schedule': '0 * * * *',  # Every hour
                'log': 'cron_accounts.log',
                'comment': f'inboxassure_{env}_accounts'
            },
            {
                'command': 'launch_bison_scheduled_spamchecks',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_bison_launch.log',
                'comment': f'inboxassure_{env}_bison_launch'
            },
            {
                'command': 'generate_spamcheck_bison_reports',
                'schedule': '*/5 * * * *',  # Every 5 minutes
                'log': 'cron_bison_reports.log',
                'comment': f'inboxassure_{env}_bison_reports'
            }
        ]

        if options['action'] == 'list':
            self.list_jobs(env)
            return

        if options['action'] == 'setup':
            # Remove existing cron jobs for this environment only
            for job in list(cron):
                if job.comment and f'inboxassure_{env}_' in job.comment:
                    cron.remove(job)
                    self.stdout.write(f"Removed existing job: {job.comment}")

            # Ensure log directory exists
            os.makedirs(log_dir, exist_ok=True)

            # Create jobs
            for job in jobs:
                cmd = f"{python_path} {manage_py} {job['command']} >> {log_dir}/{job['log']} 2>&1"
                cron_job = cron.new(command=cmd, comment=job['comment'])
                cron_job.setall(job['schedule'])
                self.stdout.write(f"Created job: {job['command']} for {env}")

        elif options['action'] in ['enable', 'disable']:
            target_job = options['job']
            enable = options['action'] == 'enable'

            for job in cron:
                if not job.comment or not job.comment.startswith(f'inboxassure_{env}_'):
                    continue

                if target_job:
                    # Enable/disable specific job
                    matching_job = next((j for j in jobs if j['command'] == target_job), None)
                    if matching_job and job.comment == matching_job['comment']:
                        job.enable(enable)
                        status = "enabled" if enable else "disabled"
                        self.stdout.write(f"{status.capitalize()} job: {target_job} for {env}")
                        break
                else:
                    # Enable/disable all jobs
                    job.enable(enable)
                    status = "enabled" if enable else "disabled"
                    self.stdout.write(f"{status.capitalize()} job with comment: {job.comment}")

        # Write the crontab
        cron.write()
        
        self.stdout.write(self.style.SUCCESS(f'Operation completed successfully for {env} environment'))

    def list_jobs(self, env=None):
        """Helper method to list all jobs"""
        cron = CronTab(user=True)
        found_jobs = False
        
        for job in cron:
            if job.comment:
                if env and not f'inboxassure_{env}_' in job.comment:
                    continue
                    
                if job.comment.startswith('inboxassure_'):
                    found_jobs = True
                    status = "enabled" if job.is_enabled() else "disabled"
                    self.stdout.write(f"Job: {job.command} | Status: {status} | Schedule: {job.slices}")
        
        if not found_jobs:
            self.stdout.write("No InboxAssure cron jobs found" + (f" for {env} environment" if env else "")) 