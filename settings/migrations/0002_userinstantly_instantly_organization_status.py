# Generated by Django 5.0.2 on 2025-01-23 22:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='userinstantly',
            name='instantly_organization_status',
            field=models.BooleanField(default=False),
        ),
    ]