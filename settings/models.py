from django.db import models
from django.conf import settings

# Create your models here.

class UserSettings(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='settings')
    instantly_editor_email = models.EmailField(null=True, blank=True)
    instantly_editor_password = models.CharField(max_length=255, null=True, blank=True)
    instantly_user_id = models.CharField(max_length=255, null=True, blank=True)
    bison_base_url = models.URLField(null=True, blank=True)
    emailguard_api_key = models.CharField(max_length=255, null=True, blank=True)
    instantly_user_token = models.TextField(null=True, blank=True)
    instantly_status = models.BooleanField(null=True, blank=True, default=False)
    emailguard_status = models.BooleanField(null=True, blank=True, default=False)
    last_token_refresh = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_settings'
        verbose_name = 'User Settings'
        verbose_name_plural = 'User Settings'

    def __str__(self):
        return f"Settings for {self.user.email}"


class UserBison(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='bison_organizations')
    bison_organization_name = models.CharField(max_length=255)
    bison_organization_api_key = models.CharField(max_length=255)
    bison_organization_status = models.BooleanField(null=True, blank=True, default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_bison'
        verbose_name = 'User Bison'
        verbose_name_plural = 'User Bison'

    def __str__(self):
        return f"{self.bison_organization_name} - {self.user.email}"


class UserInstantly(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='instantly_organizations')
    instantly_organization_id = models.CharField(max_length=255)
    instantly_organization_name = models.CharField(max_length=255)
    instantly_organization_token = models.TextField(null=True, blank=True)
    instantly_organization_status = models.BooleanField(null=True, blank=True, default=False)
    instantly_api_key = models.CharField(max_length=255, null=True, blank=True)
    last_token_refresh = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'user_instantly'
        verbose_name = 'User Instantly'
        verbose_name_plural = 'User Instantly'

    def __str__(self):
        return f"{self.instantly_organization_name} - {self.user.email}"
