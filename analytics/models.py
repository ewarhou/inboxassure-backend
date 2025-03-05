from django.db import models
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from settings.models import UserBison

User = get_user_model()

class InboxassureOrganizations(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False, db_index=True)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inboxassure_organizations'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['id']),
        ]

class ClientOrganizations(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.UUIDField()
    organization = models.ForeignKey(InboxassureOrganizations, on_delete=models.CASCADE, db_column='organization_id')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'client_organizations'
        ordering = ['-created_at']

class InboxassureReports(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.UUIDField()
    organization_id = models.UUIDField()
    total_accounts = models.IntegerField(default=0)
    sending_power = models.IntegerField(default=0)
    google_good = models.IntegerField(default=0)
    google_bad = models.IntegerField(default=0)
    outlook_good = models.IntegerField(default=0)
    outlook_bad = models.IntegerField(default=0)
    report_datetime = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inboxassure_reports'
        ordering = ['-report_datetime']

class ProviderPerformance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    report_id = models.UUIDField()
    provider = models.CharField(max_length=255)
    total_checked_inboxes = models.IntegerField(default=0)
    good_accounts_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    bad_accounts_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    google_good_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    google_bad_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    outlook_good_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    outlook_bad_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    reply_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    bounce_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'provider_performance'
        ordering = ['-created_at']

class UserCampaignsBison(models.Model):
    """
    Stores pre-calculated campaign data from Bison to improve API response time.
    This table is updated whenever a Bison spamcheck is completed.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bison_campaigns')
    bison_organization = models.ForeignKey('settings.UserBison', on_delete=models.CASCADE, related_name='campaigns')
    campaign_id = models.IntegerField(help_text="Campaign ID from Bison API")
    campaign_name = models.CharField(max_length=255, help_text="Campaign name from Bison API")
    connected_emails_count = models.IntegerField(default=0, help_text="Number of emails connected to this campaign")
    sends_per_account = models.IntegerField(default=0, help_text="Average number of sends per account")
    google_score = models.FloatField(default=0, help_text="Google deliverability score (0-100)")
    outlook_score = models.FloatField(default=0, help_text="Outlook deliverability score (0-100)")
    max_daily_sends = models.IntegerField(default=0, help_text="Maximum daily sends")
    last_updated = models.DateTimeField(auto_now=True, help_text="When this record was last updated")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_campaigns_bison'
        verbose_name = 'User Campaign Bison'
        verbose_name_plural = 'User Campaigns Bison'
        unique_together = ('bison_organization', 'campaign_id')

    def __str__(self):
        return f"{self.campaign_name} ({self.campaign_id}) - {self.bison_organization.bison_organization_name}"

class UserBisonDashboardSummary(models.Model):
    """
    Model to store pre-calculated dashboard summary data for Bison organizations.
    New records are inserted rather than updating existing ones.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bison_dashboard_summaries')
    bison_organization = models.ForeignKey(UserBison, on_delete=models.CASCADE)
    checked_accounts = models.IntegerField(default=0)
    at_risk_accounts = models.IntegerField(default=0)
    protected_accounts = models.IntegerField(default=0)
    spam_emails_count = models.IntegerField(default=0)
    inbox_emails_count = models.IntegerField(default=0)
    spam_emails_percentage = models.FloatField(default=0.0)
    inbox_emails_percentage = models.FloatField(default=0.0)
    overall_deliverability = models.FloatField(default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['user', 'bison_organization', '-created_at']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Dashboard Summary for {self.bison_organization.bison_organization_name} ({self.created_at})"

class UserBisonProviderPerformance(models.Model):
    """
    Model to store pre-calculated provider performance data for Bison organizations.
    New records are inserted rather than updating existing ones.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bison_provider_performances')
    bison_organization = models.ForeignKey(UserBison, on_delete=models.CASCADE)
    provider_name = models.CharField(max_length=255)
    start_date = models.DateField()
    end_date = models.DateField()
    total_accounts = models.IntegerField(default=0)
    google_score = models.FloatField(default=0.0)
    outlook_score = models.FloatField(default=0.0)
    overall_score = models.FloatField(default=0.0)
    sending_power = models.IntegerField(default=0)
    emails_sent_count = models.IntegerField(default=0, help_text='Total number of emails sent')
    bounced_count = models.IntegerField(default=0, help_text='Number of bounced emails')
    unique_replied_count = models.IntegerField(default=0, help_text='Number of unique replies')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['user', 'bison_organization', 'provider_name', '-created_at']),
            models.Index(fields=['start_date', 'end_date']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Provider Performance for {self.provider_name} in {self.bison_organization.bison_organization_name} ({self.created_at})"

class UserBisonSendingPower(models.Model):
    """
    Model to store pre-calculated sending power data for Bison organizations.
    New records are inserted rather than updating existing ones.
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bison_sending_powers')
    bison_organization = models.ForeignKey(UserBison, on_delete=models.CASCADE)
    report_date = models.DateField()
    sending_power = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['user', 'bison_organization', 'report_date', '-created_at']),
        ]
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Sending Power for {self.bison_organization.bison_organization_name} on {self.report_date} ({self.created_at})" 