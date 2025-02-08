#!/bin/bash

# Function to show usage
show_usage() {
    echo "Usage: $0 [enable|disable] [all|launch|reports|recurring|accounts|campaigns]"
    echo "Examples:"
    echo "  $0 disable all        # Disable all cron jobs"
    echo "  $0 enable all         # Enable all cron jobs"
    echo "  $0 disable launch     # Disable only launch_scheduled job"
    echo "  $0 enable reports     # Enable only generate_reports job"
}

# Check arguments
if [ $# -ne 2 ]; then
    show_usage
    exit 1
fi

ACTION=$1
TARGET=$2

# Temporary file for crontab
TEMP_CRONTAB="/tmp/temp_crontab"

# Get current crontab
crontab -l > $TEMP_CRONTAB

case $ACTION in
    "enable")
        case $TARGET in
            "all")
                # First, remove any double comments
                sed -i 's/^##*/#/' $TEMP_CRONTAB
                # Then remove single comments
                sed -i 's/^#//' $TEMP_CRONTAB
                echo "Enabled all cron jobs"
                ;;
            "launch")
                sed -i '/launch_scheduled.sh/ {s/^##*/#/; s/^#//}' $TEMP_CRONTAB
                echo "Enabled launch_scheduled cron job"
                ;;
            "reports")
                sed -i '/generate_reports.sh/ {s/^##*/#/; s/^#//}' $TEMP_CRONTAB
                echo "Enabled generate_reports cron job"
                ;;
            "recurring")
                sed -i '/recurring_spamchecks.sh/ {s/^##*/#/; s/^#//}' $TEMP_CRONTAB
                echo "Enabled recurring_spamchecks cron job"
                ;;
            "accounts")
                sed -i '/update_accounts.sh/ {s/^##*/#/; s/^#//}' $TEMP_CRONTAB
                echo "Enabled update_accounts cron job"
                ;;
            "campaigns")
                sed -i '/check_campaigns.sh/ {s/^##*/#/; s/^#//}' $TEMP_CRONTAB
                echo "Enabled check_campaigns cron job"
                ;;
            *)
                show_usage
                exit 1
                ;;
        esac
        ;;
    "disable")
        case $TARGET in
            "all")
                # First normalize any existing comments
                sed -i 's/^##*/#/' $TEMP_CRONTAB
                # Then add comment to non-commented lines
                sed -i '/^[^#]/ s/^/#/' $TEMP_CRONTAB
                echo "Disabled all cron jobs"
                ;;
            "launch")
                sed -i '/launch_scheduled.sh/ {s/^##*/#/; s/^[^#]/#/}' $TEMP_CRONTAB
                echo "Disabled launch_scheduled cron job"
                ;;
            "reports")
                sed -i '/generate_reports.sh/ {s/^##*/#/; s/^[^#]/#/}' $TEMP_CRONTAB
                echo "Disabled generate_reports cron job"
                ;;
            "recurring")
                sed -i '/recurring_spamchecks.sh/ {s/^##*/#/; s/^[^#]/#/}' $TEMP_CRONTAB
                echo "Disabled recurring_spamchecks cron job"
                ;;
            "accounts")
                sed -i '/update_accounts.sh/ {s/^##*/#/; s/^[^#]/#/}' $TEMP_CRONTAB
                echo "Disabled update_accounts cron job"
                ;;
            "campaigns")
                sed -i '/check_campaigns.sh/ {s/^##*/#/; s/^[^#]/#/}' $TEMP_CRONTAB
                echo "Disabled check_campaigns cron job"
                ;;
            *)
                show_usage
                exit 1
                ;;
        esac
        ;;
    *)
        show_usage
        exit 1
        ;;
esac

# Install updated crontab
crontab $TEMP_CRONTAB

# Clean up
rm $TEMP_CRONTAB

# Show current crontab
echo -e "\nCurrent crontab:"
crontab -l 