# Generated by Django 5.0.2 on 2025-01-23 23:12

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('settings', '0005_alter_usersettings_instantly_user_token'),
    ]

    operations = [
        migrations.AlterField(
            model_name='userinstantly',
            name='instantly_organization_token',
            field=models.TextField(blank=True, null=True),
        ),
    ]
