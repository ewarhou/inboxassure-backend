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
