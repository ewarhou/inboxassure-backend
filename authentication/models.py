from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid

# Create your models here.

class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.expires_at:
            # Set expiration to 1 hour from creation
            self.expires_at = timezone.now() + timezone.timedelta(hours=1)
        super().save(*args, **kwargs)

    def is_valid(self):
        return not self.used and timezone.now() <= self.expires_at

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user']),
        ]
