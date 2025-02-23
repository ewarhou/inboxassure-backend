# InboxAssure Cron Jobs Documentation

This document outlines all cron jobs used in the InboxAssure backend system and how to manage them.

## Available Jobs

| Job Name | Schedule | Description | Log File |
|----------|----------|-------------|-----------|
| handle_recurring_spamchecks | Every hour | Handles recurring spam check tasks | /var/log/inboxassure/cron_recurring.log |
| launch_instantly_scheduled_spamchecks | Every 5 minutes | Launches scheduled instant spam checks | /var/log/inboxassure/cron_launch.log |
| generate_spamcheck_instantly_reports | Every 5 minutes | Generates reports for instant spam checks | /var/log/inboxassure/cron_reports.log |
| update_active_accounts | Every hour | Updates status of active accounts | /var/log/inboxassure/cron_accounts.log |
| launch_bison_scheduled_spamchecks | Every 5 minutes | Launches scheduled Bison spam checks | /var/log/inboxassure/cron_bison_launch.log |
| generate_spamcheck_bison_reports | Every 5 minutes | Generates reports for Bison spam checks | /var/log/inboxassure/cron_bison_reports.log |

## Managing Cron Jobs

All commands should be run on the server. Connect to the server first:
```bash
ssh -i ~/.ssh/inboxassure root@68.183.98.54
```

Then navigate to the project directory and activate the virtual environment:
```bash
cd /var/www/inboxassure-backend
source venv/bin/activate
```

### Available Commands

1. **Setup All Jobs**
   ```bash
   python manage.py setup_crons --action setup
   ```
   This command will:
   - Remove all existing cron jobs
   - Create log directory if it doesn't exist
   - Set up all jobs with their respective schedules

2. **Enable/Disable Specific Job**
   ```bash
   # Enable a specific job
   python manage.py setup_crons --action enable --job handle_recurring_spamchecks

   # Disable a specific job
   python manage.py setup_crons --action disable --job handle_recurring_spamchecks
   ```

3. **Enable/Disable All Jobs**
   ```bash
   # Enable all jobs
   python manage.py setup_crons --action enable

   # Disable all jobs
   python manage.py setup_crons --action disable
   ```

## Log Files

All job logs are stored in `/var/log/inboxassure/` directory. Each job has its own log file:

- Recurring checks: `cron_recurring.log`
- Instant checks: `cron_launch.log`
- Instant reports: `cron_reports.log`
- Account updates: `cron_accounts.log`
- Bison checks: `cron_bison_launch.log`
- Bison reports: `cron_bison_reports.log`

To view logs in real-time:
```bash
tail -f /var/log/inboxassure/cron_*.log
```

## Cron Schedule Format

The cron schedule format used is standard cron format:
```
* * * * *
│ │ │ │ │
│ │ │ │ └── Day of the week (0-6, Sunday=0)
│ │ │ └──── Month (1-12)
│ │ └────── Day of the month (1-31)
│ └──────── Hour (0-23)
└────────── Minute (0-59)
```

Examples:
- `0 * * * *` - Every hour at minute 0
- `*/5 * * * *` - Every 5 minutes
- `0 0 * * *` - Every day at midnight

## Troubleshooting

1. If jobs are not running:
   ```bash
   # Check if cron service is running
   systemctl status cron

   # View cron logs
   tail -f /var/log/syslog | grep CRON
   ```

2. To check current cron configuration:
   ```bash
   crontab -l
   ```

3. To manually check job status:
   ```bash
   # Example for checking recurring spam checks
   cd /var/www/inboxassure-backend
   source venv/bin/activate
   python manage.py handle_recurring_spamchecks
   ```

## Important Notes

- All times are in UTC
- Logs are rotated automatically to prevent disk space issues
- Jobs are identified by their unique comments (e.g., 'inboxassure_recurring')
- All jobs run under the root user
- Changes to job schedules require updating the `setup_crons.py` file and running the setup command again 