# Generated by Django 5.0.2 on 2025-03-15 22:08

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0032_alter_userspamcheckbison_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='userspamcheckbison',
            name='update_sending_limit',
            field=models.BooleanField(default=True, help_text='Whether to update sending limits in Bison API based on scores'),
        ),
    ]
