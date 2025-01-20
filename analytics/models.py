from django.db import models
import uuid

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

class ClientOrganizations(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_id = models.UUIDField()
    organization_id = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'client_organizations'
        ordering = ['-created_at']

class InboxassureOrganizations(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'inboxassure_organizations'
        ordering = ['-created_at'] 