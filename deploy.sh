#!/bin/bash

echo "🚀 Starting deployment..."

ssh -i ~/.ssh/inboxassure root@68.183.98.54 "\
    cd /var/www/inboxassure-backend && \
    git pull origin main && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    python manage.py migrate && \
    systemctl restart gunicorn && \
    systemctl restart nginx"

echo "✅ Done!" 