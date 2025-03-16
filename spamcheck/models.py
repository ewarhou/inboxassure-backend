from django.db import models
from django.conf import settings
from django.core.validators import EmailValidator, MinValueValidator, MaxValueValidator
import uuid
from django.utils import timezone

# Create your models here.

class UserSpamcheckCampaignOptions(models.Model):
    id = models.BigAutoField(primary_key=True)
    spamcheck = models.ForeignKey('UserSpamcheck', on_delete=models.CASCADE, related_name='campaign_options')
    open_tracking = models.BooleanField(default=False)
    link_tracking = models.BooleanField(default=False)
    text_only = models.BooleanField(default=False)
    subject = models.CharField(max_length=255)
    body = models.TextField()

    class Meta:
        db_table = 'user_spamcheck_campaign_instantly_options'
        verbose_name = 'User Spamcheck Campaign Option'
        verbose_name_plural = 'User Spamcheck Campaign Options'
        ordering = ['-id']

    def __str__(self):
        return f"Options for {self.spamcheck.name}"


class UserSpamcheck(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('generating_reports', 'Generating Reports'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('paused', 'Paused')
    ]
    
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamchecks')
    user_organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE, related_name='spamchecks', null=True)
    options = models.OneToOneField('UserSpamcheckCampaignOptions', on_delete=models.CASCADE, null=True, blank=True, related_name='spamcheck_instance')
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='pending')
    is_domain_based = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    recurring_days = models.IntegerField(null=True, blank=True)
    conditions = models.CharField(max_length=255, null=True, blank=True)
    reports_waiting_time = models.FloatField(null=True, blank=True, default=1.0, help_text="Time in hours to wait before generating reports (0 for immediate, 0.5 for 30min, 1 for 1h, etc). Default is 1h")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_instantly'
        verbose_name = 'User Spamcheck'
        verbose_name_plural = 'User Spamchecks'
        ordering = ['-created_at']
        unique_together = ['user', 'user_organization', 'name']

    def __str__(self):
        return f"{self.name} - {self.user.email}"

    def save(self, *args, **kwargs):
        # Force update of updated_at on every save
        self.updated_at = timezone.now()
        super().save(*args, **kwargs)


class UserSpamcheckAccounts(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamcheck_accounts')
    organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE)
    spamcheck = models.ForeignKey(UserSpamcheck, on_delete=models.CASCADE, related_name='accounts')
    email_account = models.EmailField(max_length=255, validators=[EmailValidator()], null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.email_account:
            EmailValidator()(self.email_account)
        super().clean()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'user_spamcheck_accounts_instantly'
        verbose_name = 'User Spamcheck Account'
        verbose_name_plural = 'User Spamcheck Accounts'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.spamcheck.name} - {self.email_account or 'No Email'}"


class UserSpamcheckCampaigns(models.Model):
    CAMPAIGN_STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('deleted', 'Deleted')
    ]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamcheck_campaigns')
    spamcheck = models.ForeignKey(UserSpamcheck, on_delete=models.CASCADE, related_name='campaigns')
    organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE)
    account_id = models.ForeignKey(UserSpamcheckAccounts, on_delete=models.CASCADE, related_name='campaigns', null=True, blank=True, db_column='account_id')
    instantly_campaign_id = models.CharField(max_length=255)
    emailguard_tag = models.CharField(max_length=255)
    campaign_status = models.CharField(max_length=20, choices=CAMPAIGN_STATUS_CHOICES, default='active')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_campaigns_instantly'
        verbose_name = 'User Spamcheck Campaign'
        verbose_name_plural = 'User Spamcheck Campaigns'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.spamcheck.name} - {self.account_id.email_account if self.account_id else 'No Account'}"


class UserSpamcheckReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        'settings.UserInstantly',
        on_delete=models.CASCADE,
        related_name='spamcheck_reports'
    )
    email_account = models.EmailField()
    report_link = models.URLField()
    google_pro_score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(4)]
    )
    outlook_pro_score = models.DecimalField(
        max_digits=3,
        decimal_places=1,
        validators=[MinValueValidator(0), MaxValueValidator(4)]
    )
    is_good = models.BooleanField(default=False, help_text='Whether this account meets the spamcheck conditions')
    spamcheck_instantly = models.ForeignKey(
        'UserSpamcheck',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports'
    )
    used_subject = models.TextField(null=True, blank=True, help_text='Subject used in the spamcheck campaign')
    used_body = models.TextField(null=True, blank=True, help_text='Body used in the spamcheck campaign')
    sending_limit = models.IntegerField(null=True, blank=True, help_text='Sending limit used in the campaign')
    tags_uuid_list = models.TextField(null=True, blank=True, help_text='List of tag UUIDs used in the campaign')
    instantly_workspace_uuid = models.CharField(max_length=255, null=True, blank=True, help_text='UUID of the Instantly workspace')
    bison_workspace_uuid = models.CharField(max_length=255, null=True, blank=True, help_text='UUID of the Bison workspace')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_spamcheck_reports'
        ordering = ['-created_at']

    def __str__(self):
        return f"Spam Check Report for {self.email_account}"


class UserSpamcheckBison(models.Model):
    STATUS_CHOICES = [
        ('queued', 'Queued'),
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('waiting_for_reports', 'Waiting For Reports'),
        ('generating_reports', 'Generating Reports'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('paused', 'Paused')
    ]
    
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bison_spamchecks')
    user_organization = models.ForeignKey('settings.UserBison', on_delete=models.CASCADE, related_name='bison_spamchecks', null=True)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='queued')
    is_domain_based = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    recurring_days = models.IntegerField(null=True, blank=True)
    weekdays = models.CharField(max_length=21, null=True, blank=True, help_text="Comma-separated list of weekdays (0=Monday, 6=Sunday) when this spamcheck should run")
    conditions = models.CharField(max_length=255, null=True, blank=True)
    reports_waiting_time = models.FloatField(null=True, blank=True, default=1.0, help_text="Time in hours to wait before generating reports (0 for immediate, 0.5 for 30min, 1-12 for 1-12 hours). Default is 1h")
    update_sending_limit = models.BooleanField(default=True, help_text="Whether to update sending limits in Bison API based on scores")
    plain_text = models.BooleanField(default=False)
    subject = models.TextField(help_text='Email subject template')
    body = models.TextField(help_text='Email body template')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_bison'
        verbose_name = 'User Spamcheck Bison'
        verbose_name_plural = 'User Spamcheck Bison'
        ordering = ['-created_at']
        unique_together = ['user', 'user_organization', 'name']

    def __str__(self):
        return f"{self.name} - {self.user.email}"

    def save(self, *args, **kwargs):
        # Force update of updated_at on every save
        self.updated_at = timezone.now()
        super().save(*args, **kwargs)


class UserSpamcheckAccountsBison(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bison_spamcheck_accounts')
    organization = models.ForeignKey('settings.UserBison', on_delete=models.CASCADE)
    bison_spamcheck = models.ForeignKey(UserSpamcheckBison, on_delete=models.CASCADE, related_name='accounts')
    email_account = models.EmailField(max_length=255, validators=[EmailValidator()], null=True, blank=True)
    last_emailguard_tag = models.CharField(max_length=255, null=True, blank=True, help_text='Last used EmailGuard tag UUID for this account')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        if self.email_account:
            EmailValidator()(self.email_account)
        super().clean()

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = 'user_spamcheck_accounts_bison'
        verbose_name = 'User Spamcheck Account Bison'
        verbose_name_plural = 'User Spamcheck Accounts Bison'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.bison_spamcheck.name} - {self.email_account or 'No Email'}"


class UserSpamcheckBisonReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bison_organization = models.ForeignKey(
        'settings.UserBison',
        on_delete=models.CASCADE,
        related_name='bison_spamcheck_reports'
    )
    email_account = models.EmailField()
    report_link = models.URLField()
    google_pro_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(1)]
    )
    outlook_pro_score = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        validators=[MinValueValidator(0), MaxValueValidator(1)]
    )
    is_good = models.BooleanField(default=False, help_text='Whether this account meets the spamcheck conditions')
    spamcheck_bison = models.ForeignKey(
        'UserSpamcheckBison',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='reports'
    )
    used_subject = models.TextField(null=True, blank=True, help_text='Subject used in the spamcheck campaign')
    used_body = models.TextField(null=True, blank=True, help_text='Body used in the spamcheck campaign')
    sending_limit = models.IntegerField(null=True, blank=True, help_text='Sending limit used in the campaign')
    tags_list = models.TextField(null=True, blank=True, help_text='List of tags used in the campaign')
    workspace_name = models.CharField(max_length=255, null=True, blank=True, help_text='Name of the workspace')
    bounced_count = models.IntegerField(default=0, help_text='Number of bounced emails')
    unique_replied_count = models.IntegerField(default=0, help_text='Number of unique replies')
    emails_sent_count = models.IntegerField(default=0, help_text='Total number of emails sent')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_spamcheck_bison_reports'
        ordering = ['-created_at']

    def __str__(self):
        return f"Bison Report for {self.email_account} ({self.id})"


class SpamcheckErrorLog(models.Model):
    """
    Model to log errors that occur during spamcheck execution.
    This helps in debugging and troubleshooting failed spamchecks.
    """
    ERROR_TYPES = [
        ('api_error', 'API Error'),
        ('connection_error', 'Connection Error'),
        ('authentication_error', 'Authentication Error'),
        ('validation_error', 'Validation Error'),
        ('timeout_error', 'Timeout Error'),
        ('unknown_error', 'Unknown Error')
    ]
    
    PROVIDER_CHOICES = [
        ('instantly', 'Instantly'),
        ('emailguard', 'EmailGuard'),
        ('bison', 'Bison'),
        ('system', 'System')
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamcheck_error_logs')
    spamcheck = models.ForeignKey(
        UserSpamcheck, 
        on_delete=models.CASCADE, 
        related_name='error_logs',
        null=True,
        blank=True,
        help_text='The spamcheck that encountered the error'
    )
    bison_spamcheck = models.ForeignKey(
        UserSpamcheckBison, 
        on_delete=models.CASCADE, 
        related_name='error_logs',
        null=True,
        blank=True,
        help_text='The Bison spamcheck that encountered the error'
    )
    error_type = models.CharField(max_length=50, choices=ERROR_TYPES, default='unknown_error')
    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default='system')
    error_message = models.TextField(help_text='The error message returned by the API or system')
    error_details = models.JSONField(null=True, blank=True, help_text='Additional details about the error in JSON format')
    account_email = models.EmailField(null=True, blank=True, help_text='The email account being processed when the error occurred')
    step = models.CharField(max_length=255, null=True, blank=True, help_text='The step in the process where the error occurred')
    api_endpoint = models.CharField(max_length=255, null=True, blank=True, help_text='The API endpoint that returned the error')
    status_code = models.IntegerField(null=True, blank=True, help_text='The HTTP status code returned by the API')
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'spamcheck_error_logs'
        verbose_name = 'Spamcheck Error Log'
        verbose_name_plural = 'Spamcheck Error Logs'
        ordering = ['-created_at']
    
    def __str__(self):
        spamcheck_name = self.spamcheck.name if self.spamcheck else (self.bison_spamcheck.name if self.bison_spamcheck else "Unknown")
        return f"Error in {spamcheck_name}: {self.error_type} - {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}"
