INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_ninja',
    'corsheaders',
    'rest_framework',
    'django_crontab',
    'spamcheck',
    'accounts',
]

# Cron Jobs Configuration
CRONJOBS = [
    ('0 * * * *', 'django.core.management.call_command', ['handle_recurring_spamchecks'], {}, '>> /var/log/inboxassure/cron_recurring.log 2>&1'),
    ('* * * * *', 'django.core.management.call_command', ['launch_instantly_scheduled_spamchecks'], {}, '>> /var/log/inboxassure/cron_launch.log 2>&1'),
    ('*/5 * * * *', 'django.core.management.call_command', ['generate_spamcheck_instantly_reports'], {}, '>> /var/log/inboxassure/cron_reports.log 2>&1'),
    ('0 * * * *', 'django.core.management.call_command', ['update_active_accounts'], {}, '>> /var/log/inboxassure/cron_accounts.log 2>&1'),
    ('*/5 * * * *', 'django.core.management.call_command', ['check_spamcheck_instantly_campaigns_status'], {}, '>> /var/log/inboxassure/cron_campaigns.log 2>&1'),
    ('* * * * *', 'django.core.management.call_command', ['launch_bison_scheduled_spamchecks']),
    ('*/5 * * * *', 'django.core.management.call_command', ['generate_spamcheck_bison_reports']),
]

# Ensure log directory exists
CRONTAB_COMMAND_PREFIX = 'mkdir -p /var/log/inboxassure;' 