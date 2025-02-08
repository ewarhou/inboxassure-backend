#!/bin/bash

# Activate virtual environment
source /var/www/inboxassure-backend/venv/bin/activate

# Change to project directory
cd /var/www/inboxassure-backend

# Run command
python manage.py handle_recurring_spamchecks

# Deactivate virtual environment
deactivate 