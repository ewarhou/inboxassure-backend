from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta
import requests
from settings.models import UserSettings, UserInstantly

class Command(BaseCommand):
    help = 'Refresh Instantly tokens for users and organizations'

    def handle(self, *args, **kwargs):
        # Get all active users with Instantly configured
        active_settings = UserSettings.objects.filter(
            instantly_editor_email__isnull=False,
            instantly_editor_password__isnull=False
        )
        
        refresh_count = 0
        for settings in active_settings:
            # Check if token needs refresh (older than 1 minute for testing)
            if not settings.last_token_refresh or \
               timezone.now() - settings.last_token_refresh > timedelta(minutes=1):
                
                print(f"\nRefreshing tokens for user: {settings.user.email}")
                
                # Login to Instantly
                login_response = requests.post('https://app.instantly.ai/api/auth/login', json={
                    'email': settings.instantly_editor_email,
                    'password': settings.instantly_editor_password
                })
                
                if login_response.status_code == 200:
                    session_token = login_response.cookies.get('__session')
                    if session_token:
                        print("User token refreshed successfully")
                        settings.instantly_user_token = session_token
                        settings.last_token_refresh = timezone.now()
                        settings.save()
                        
                        # Refresh organization tokens
                        headers = {
                            'Cookie': f'__session={session_token}',
                            'Content-Type': 'application/json'
                        }
                        
                        # Get organizations
                        orgs_response = requests.get('https://app.instantly.ai/api/organization/user', headers=headers)
                        if orgs_response.status_code == 200:
                            organizations = orgs_response.json()
                            
                            for org in organizations:
                                print(f"\nRefreshing token for organization: {org['name']}")
                                
                                # Get new organization token
                                auth_response = requests.post(
                                    'https://app.instantly.ai/api/organization/auth_workspace',
                                    headers=headers,
                                    json={'orgID': org['id']}
                                )
                                
                                if auth_response.status_code == 200:
                                    org_token = auth_response.json().get('org_access')
                                    if org_token:
                                        # Verify organization access
                                        verify_headers = {
                                            'X-Org-Auth': org_token,
                                            'Cookie': f'__session={session_token}',
                                            'Content-Type': 'application/json'
                                        }
                                        verify_body = {
                                            "search": "",
                                            "limit": 1,
                                            "filter": None,
                                            "skip": 0,
                                            "include_tags": False
                                        }
                                        verify_response = requests.post(
                                            'https://app.instantly.ai/backend/api/v1/account/list',
                                            headers=verify_headers,
                                            json=verify_body
                                        )
                                        
                                        org_status = verify_response.status_code == 200
                                        
                                        # Update organization token
                                        UserInstantly.objects.update_or_create(
                                            user=settings.user,
                                            instantly_organization_id=org['id'],
                                            defaults={
                                                'instantly_organization_name': org['name'],
                                                'instantly_organization_token': org_token,
                                                'instantly_organization_status': org_status,
                                                'last_token_refresh': timezone.now()
                                            }
                                        )
                                        print(f"Organization token refreshed successfully")
                                        refresh_count += 1
                
        self.stdout.write(
            self.style.SUCCESS(f'\nSuccessfully refreshed {refresh_count} tokens')
        ) 