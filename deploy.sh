#!/bin/bash

echo "🚀 Starting deployment process..."

# Local machine deployment
echo "💻 Local machine: Committing and pushing changes..."
git add .
git commit -m "Update: Modified login endpoint to use email instead of username"
git push origin main

# Remote server deployment
echo "🌐 Remote server: Deploying to DigitalOcean..."
ssh -i ~/.ssh/inboxassure root@68.183.98.54 "\
    cd /var/www/inboxassure-backend && \
    git pull origin main && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    python manage.py migrate && \
    systemctl restart gunicorn \
"

echo "✅ Deployment completed!" 