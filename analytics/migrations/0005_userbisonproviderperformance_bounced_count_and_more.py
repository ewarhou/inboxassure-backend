# Generated by Django 5.0.2 on 2025-03-05 02:17

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0004_userbisonproviderperformance_userbisonsendingpower_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='userbisonproviderperformance',
            name='bounced_count',
            field=models.IntegerField(default=0, help_text='Number of bounced emails'),
        ),
        migrations.AddField(
            model_name='userbisonproviderperformance',
            name='emails_sent_count',
            field=models.IntegerField(default=0, help_text='Total number of emails sent'),
        ),
        migrations.AddField(
            model_name='userbisonproviderperformance',
            name='unique_replied_count',
            field=models.IntegerField(default=0, help_text='Number of unique replies'),
        ),
    ]
