#!/bin/bash

echo "🚀 Starting production deployment..."

# Execute commands directly since we're already on the server
cd /var/www/inboxassure-production

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
chown -R www-data:www-data /var/www/inboxassure-production/static/
chown -R www-data:www-data /var/www/inboxassure-production/media/

# Set proper permissions
chmod -R 755 /var/www/inboxassure-production/static/
chmod -R 755 /var/www/inboxassure-production/media/

echo "6️⃣ Restarting services..."
systemctl restart gunicorn
systemctl restart nginx

echo "✅ Production deployment completed successfully!" 