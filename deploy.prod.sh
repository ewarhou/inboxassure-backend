#!/bin/bash

echo "🚀 Starting deployment..."

# 1. Activate virtual environment
echo "1️⃣ Activating virtual environment..."
source venv/bin/activate

# 2. Install/update dependencies
echo "2️⃣ Installing/updating dependencies..."
pip install -r requirements.txt

# 3. Run database migrations
echo "3️⃣ Running database migrations..."
python manage.py migrate

# 4. Collect static files
echo "4️⃣ Collecting static files..."
python manage.py collectstatic --noinput

# 5. Set proper permissions
echo "5️⃣ Setting proper permissions..."
chown -R www-data:www-data /var/www/inboxassure-production/static/
chown -R www-data:www-data /var/www/inboxassure-production/media/

# 6. Restart services
echo "6️⃣ Restarting services..."
chmod -R 755 /var/www/inboxassure-production/static/
chmod -R 755 /var/www/inboxassure-production/media/
systemctl restart gunicorn
systemctl restart nginx

echo "✅ Deployment completed successfully!" 