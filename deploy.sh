#!/bin/bash

echo "🚀 Starting deployment process..."

# Define log file
LOG_FILE="/var/www/inboxassure-backend/deployment.log"
GUNICORN_LOG="/var/www/inboxassure-backend/gunicorn.log"
APP_LOG="/var/www/inboxassure-backend/app.log"

# Function to log messages
log_message() {
    echo "$1"
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Deploy to DigitalOcean
log_message "🌐 Remote server: Deploying to DigitalOcean..."

# SSH into the server and execute commands
ssh -i ~/.ssh/inboxassure root@68.183.98.54 << 'ENDSSH'
    cd /var/www/inboxassure-backend

    # Create log files if they don't exist
    touch deployment.log gunicorn.log app.log
    chown www-data:www-data deployment.log gunicorn.log app.log
    chmod 664 deployment.log gunicorn.log app.log

    # Pull latest changes
    git fetch origin main
    git reset --hard origin/main

    # Activate virtual environment and install requirements
    source venv/bin/activate
    pip install -r requirements.txt >> deployment.log 2>&1

    # Apply migrations
    python manage.py migrate >> deployment.log 2>&1

    # Update Gunicorn configuration to include logging
    cat > /etc/systemd/system/gunicorn.service << 'EOL'
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/inboxassure-backend
ExecStart=/var/www/inboxassure-backend/venv/bin/gunicorn \
          --access-logfile /var/www/inboxassure-backend/gunicorn.log \
          --error-logfile /var/www/inboxassure-backend/gunicorn.log \
          --capture-output \
          --log-level debug \
          --workers 4 \
          --bind unix:/run/gunicorn.sock \
          inboxassure.wsgi:application

[Install]
WantedBy=multi-user.target
EOL

    # Reload systemd and restart Gunicorn
    systemctl daemon-reload
    systemctl restart gunicorn
    systemctl restart nginx

    # Log deployment completion
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Deployment completed successfully" >> deployment.log
ENDSSH

echo "✅ Deployment completed!" 