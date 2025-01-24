from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserSettings

User = get_user_model()

@receiver(post_save, sender=User)
def create_user_settings(sender, instance, created, **kwargs):
    """Create UserSettings for new users with default values"""
    if created:
        UserSettings.objects.create(
            user=instance,
            bison_base_url='https://app.orbitmailboost.com',
            instantly_status=False,
            emailguard_status=False
        ) 