#!/bin/bash

echo "🚀 Starting deployment..."

# Execute commands directly since we're already on the server
cd /var/www/inboxassure-backend
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
systemctl restart gunicorn
systemctl restart nginx

echo "✅ Done!" 