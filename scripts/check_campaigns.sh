#!/bin/bash

# Activate virtual environment
source /var/www/inboxassure-backend/venv/bin/activate

# Change to project directory
cd /var/www/inboxassure-backend

# Run command
python manage.py check_spamcheck_instantly_campaigns_status

# Deactivate virtual environment
deactivate 