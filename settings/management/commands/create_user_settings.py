from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from settings.models import UserSettings

User = get_user_model()

class Command(BaseCommand):
    help = 'Create UserSettings for existing users who don\'t have them'

    def handle(self, *args, **kwargs):
        users_without_settings = User.objects.filter(settings=None)
        created_count = 0

        for user in users_without_settings:
            UserSettings.objects.create(
                user=user,
                bison_base_url='https://app.orbitmailboost.com',
                instantly_status=False,
                emailguard_status=False
            )
            created_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'Successfully created settings for {created_count} users'
            )
        ) 