# Generated by Django 5.0.2 on 2025-02-25 19:46

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('analytics', '0002_auto_20250225_1521'),
        ('settings', '0012_alter_userbison_options_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserBisonDashboardSummary',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('checked_accounts', models.IntegerField(default=0, help_text='Number of accounts checked')),
                ('at_risk_accounts', models.IntegerField(default=0, help_text='Number of accounts at risk')),
                ('protected_accounts', models.IntegerField(default=0, help_text='Number of protected accounts')),
                ('spam_emails_count', models.IntegerField(default=0, help_text='Estimated number of emails going to spam')),
                ('inbox_emails_count', models.IntegerField(default=0, help_text='Estimated number of emails going to inbox')),
                ('spam_emails_percentage', models.FloatField(default=0, help_text='Percentage of emails going to spam')),
                ('inbox_emails_percentage', models.FloatField(default=0, help_text='Percentage of emails going to inbox')),
                ('overall_deliverability', models.FloatField(default=0, help_text='Overall deliverability score (0-100)')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('bison_organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dashboard_summaries', to='settings.userbison')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='bison_dashboard_summaries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User Bison Dashboard Summary',
                'verbose_name_plural': 'User Bison Dashboard Summaries',
                'db_table': 'user_bison_dashboard_summary',
                'ordering': ['-created_at'],
                'indexes': [models.Index(fields=['user', 'bison_organization', '-created_at'], name='user_bison__user_id_0a4b86_idx')],
            },
        ),
    ]
