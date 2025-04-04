# Generated by Django 5.0.2 on 2025-04-04 21:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0038_alter_spamcheckerrorlog_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='spamcheckerrorlog',
            name='tags',
            field=models.JSONField(blank=True, help_text='The tags associated with this account', null=True),
        ),
        migrations.AddField(
            model_name='spamcheckerrorlog',
            name='workspace_id',
            field=models.CharField(blank=True, help_text='The workspace ID this account belongs to', max_length=255, null=True),
        ),
    ]
