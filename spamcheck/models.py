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
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('generating_reports', 'Generating Reports'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('paused', 'Paused')
    ]
    
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bison_spamchecks')
    user_organization = models.ForeignKey('settings.UserBison', on_delete=models.CASCADE, related_name='bison_spamchecks', null=True)
    name = models.CharField(max_length=255)
    status = models.CharField(max_length=50, choices=STATUS_CHOICES, default='pending')
    is_domain_based = models.BooleanField(default=False)
    scheduled_at = models.DateTimeField(null=True, blank=True)
    recurring_days = models.IntegerField(null=True, blank=True)
    conditions = models.CharField(max_length=255, null=True, blank=True)
    reports_waiting_time = models.FloatField(null=True, blank=True, default=1.0, help_text="Time in hours to wait before generating reports (0 for immediate, 0.5 for 30min, 1 for 1h, etc). Default is 1h")
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'user_spamcheck_bison_reports'
        ordering = ['-created_at']

    def __str__(self):
        return f"Bison Spam Check Report for {self.email_account}"
