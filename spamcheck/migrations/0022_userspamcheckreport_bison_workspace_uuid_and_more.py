# Generated by Django 5.0.2 on 2025-02-12 22:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('spamcheck', '0021_userspamcheckreport_is_good'),
    ]

    operations = [
        migrations.AddField(
            model_name='userspamcheckreport',
            name='bison_workspace_uuid',
            field=models.CharField(blank=True, help_text='UUID of the Bison workspace', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='userspamcheckreport',
            name='instantly_workspace_uuid',
            field=models.CharField(blank=True, help_text='UUID of the Instantly workspace', max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='userspamcheckreport',
            name='sending_limit',
            field=models.IntegerField(blank=True, help_text='Sending limit used in the campaign', null=True),
        ),
        migrations.AddField(
            model_name='userspamcheckreport',
            name='tags_uuid_list',
            field=models.TextField(blank=True, help_text='List of tag UUIDs used in the campaign', null=True),
        ),
        migrations.AddField(
            model_name='userspamcheckreport',
            name='used_body',
            field=models.TextField(blank=True, help_text='Body used in the spamcheck campaign', null=True),
        ),
        migrations.AddField(
            model_name='userspamcheckreport',
            name='used_subject',
            field=models.TextField(blank=True, help_text='Subject used in the spamcheck campaign', null=True),
        ),
    ]
