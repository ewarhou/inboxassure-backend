from django.db import models
from django.contrib.auth import get_user_model
import uuid
from datetime import timedelta
from django.utils import timezone

User = get_user_model()

def profile_picture_path(instance, filename):
    ext = filename.split('.')[-1]
    return f'profile_pictures/{instance.user.id}/{uuid.uuid4()}.{ext}'

class AuthProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='auth_profile')
    profile_picture = models.ImageField(upload_to=profile_picture_path, null=True, blank=True)
    timezone = models.CharField(max_length=50, default='UTC', null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

class PasswordResetToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used = models.BooleanField(default=False)

    def is_valid(self):
        return (
            not self.used and
            self.created_at + timedelta(hours=1) > timezone.now()
        )

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['user']),
        ]
