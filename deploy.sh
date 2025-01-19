#!/bin/bash

echo "🚀 Starting deployment process..."

# Push changes to GitHub
echo "📦 Pushing changes to GitHub..."
git add .
git commit -m "Update: $1"
git push origin main

# Deploy to server
echo "🌐 Deploying to server..."
ssh -i ~/.ssh/inboxassure root@68.183.98.54 "\
    cd /var/www/inboxassure-backend && \
    git pull origin main && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    python manage.py migrate && \
    systemctl restart gunicorn \
"

echo "✅ Deployment completed!" 