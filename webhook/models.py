import uuid
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class BisonWebhook(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bison_webhooks')
    webhook_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    # payload = models.JSONField(null=True, blank=True) # Store the received webhook data
    # received_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Webhook for {self.user.username} - {self.webhook_id}"


class BisonWebhookData(models.Model):
    webhook = models.ForeignKey(BisonWebhook, on_delete=models.CASCADE, related_name='data_entries')
    payload = models.JSONField(null=True, blank=True)
    received_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"Data for webhook {self.webhook.webhook_id} at {self.received_at}"


class BisonBounces(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='bison_bounces')
    webhook = models.ForeignKey(BisonWebhook, on_delete=models.SET_NULL, null=True, blank=True, related_name='bounces')
    workspace_bison_id = models.IntegerField(null=True, blank=True) # Assuming ID is integer
    workspace_name = models.CharField(max_length=255, null=True, blank=True)
    email_subject = models.TextField(null=True, blank=True)
    email_body = models.TextField(null=True, blank=True)
    lead_email = models.EmailField()
    campaign_bison_id = models.IntegerField(null=True, blank=True) # Assuming ID is integer
    campaign_name = models.CharField(max_length=255, null=True, blank=True)
    sender_bison_id = models.IntegerField(null=True, blank=True) # Assuming ID is integer
    sender_email = models.EmailField()
    bounce_reply = models.TextField(null=True, blank=True)
    bounce_bucket = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Bounce for {self.lead_email} from campaign {self.campaign_name or self.campaign_bison_id}"
