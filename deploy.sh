#!/bin/bash

# Set Git executable path
GIT_PATH="/c/Program Files/Git/bin/git.exe"
SSH_PATH="/c/Program Files/Git/usr/bin/ssh.exe"

echo "🚀 Starting deployment process..."

# Navigate to project directory
cd "/c/Users/FatiiMa/Desktop/InboxAssure/inboxassure-backend"

# Push changes to GitHub
echo "📦 Pushing changes to GitHub..."
"$GIT_PATH" add .
"$GIT_PATH" commit -m "Update: CORS configuration"
"$GIT_PATH" push origin main

# Deploy to server
echo "🌐 Deploying to server..."
"$SSH_PATH" -i ~/.ssh/inboxassure root@68.183.98.54 "\
    cd /root/inboxassure-backend && \
    git pull origin main && \
    source venv/bin/activate && \
    pip install -r requirements.txt && \
    python manage.py migrate && \
    systemctl restart gunicorn \
"

echo "✅ Deployment completed!" 