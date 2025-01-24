from django.db import models
from django.conf import settings
import uuid

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
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamchecks')
    organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE)
    options = models.OneToOneField(UserSpamcheckCampaignOptions, on_delete=models.SET_NULL, null=True, blank=True, related_name='spamcheck_instance')
    name = models.CharField(max_length=255)
    scheduled_at = models.DateTimeField()  # Launch date and time
    recurring_days = models.IntegerField(null=True, blank=True)  # Number of days for recurring checks, null for one-time
    status = models.CharField(max_length=50, choices=[
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('generating_reports', 'Generating Reports'),
        ('completed', 'Completed'),
        ('failed', 'Failed')
    ], default='draft')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_instantly'
        verbose_name = 'User Spamcheck'
        verbose_name_plural = 'User Spamchecks'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.name} - {self.user.email}"


class UserSpamcheckAccounts(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamcheck_accounts')
    organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE)
    spamcheck = models.ForeignKey(UserSpamcheck, on_delete=models.CASCADE, related_name='accounts')
    email_account = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_accounts_instantly'
        verbose_name = 'User Spamcheck Account'
        verbose_name_plural = 'User Spamcheck Accounts'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.spamcheck.name} - {self.email_account or 'No Email'}"


class UserSpamcheckCampaigns(models.Model):
    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='spamcheck_campaigns')
    spamcheck = models.ForeignKey(UserSpamcheck, on_delete=models.CASCADE, related_name='campaigns')
    organization = models.ForeignKey('settings.UserInstantly', on_delete=models.CASCADE)
    account_id = models.ForeignKey(UserSpamcheckAccounts, on_delete=models.CASCADE, related_name='campaigns', null=True, blank=True, db_column='account_id')
    instantly_campaign_id = models.CharField(max_length=255)
    emailguard_tag = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_spamcheck_campaigns_instantly'
        verbose_name = 'User Spamcheck Campaign'
        verbose_name_plural = 'User Spamcheck Campaigns'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.spamcheck.name} - {self.account_id.email_account if self.account_id else 'No Account'}"
