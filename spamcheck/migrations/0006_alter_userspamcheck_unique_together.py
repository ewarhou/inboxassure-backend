# Generated by Django 5.0.2 on 2025-01-24 23:38

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0008_usersettings_instantly_user_id'),
        ('spamcheck', '0005_remove_userspamcheck_organization_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='userspamcheck',
            unique_together={('user', 'user_organization', 'name')},
        ),
    ]
