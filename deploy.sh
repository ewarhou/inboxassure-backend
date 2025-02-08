#!/bin/bash

echo "🚀 Starting deployment..."

# Execute commands directly since we're already on the server
cd /var/www/inboxassure-backend

echo "1️⃣ Activating virtual environment..."
source venv/bin/activate

echo "2️⃣ Installing/updating dependencies..."
pip install -r requirements.txt

echo "3️⃣ Running database migrations..."
python manage.py migrate

echo "4️⃣ Collecting static files..."
python manage.py collectstatic --noinput

echo "5️⃣ Setting proper permissions..."
# Ensure proper ownership
chown -R www-data:www-data /var/www/inboxassure-backend/static/
chown -R www-data:www-data /var/www/inboxassure-backend/media/

# Set proper permissions
chmod -R 755 /var/www/inboxassure-backend/static/
chmod -R 755 /var/www/inboxassure-backend/media/

echo "6️⃣ Restarting services..."
systemctl restart gunicorn
systemctl restart nginx

echo "✅ Deployment completed successfully!" 