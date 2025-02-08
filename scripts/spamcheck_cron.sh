#!/bin/bash

# Activate virtual environment
source /var/www/inboxassure-backend/venv/bin/activate

# Change to project directory
cd /var/www/inboxassure-backend

# Run spamcheck management commands
echo "Running handle_recurring_spamchecks..."
python manage.py handle_recurring_spamchecks

echo "Running launch_instantly_scheduled_spamchecks..."
python manage.py launch_instantly_scheduled_spamchecks

echo "Running generate_spamcheck_instantly_reports..."
python manage.py generate_spamcheck_instantly_reports

echo "Running update_active_accounts..."
python manage.py update_active_accounts

# Deactivate virtual environment
deactivate 