# Generated by Django 5.0.2 on 2025-01-24 12:58

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0007_userinstantly_last_token_refresh_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersettings',
            name='instantly_user_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
