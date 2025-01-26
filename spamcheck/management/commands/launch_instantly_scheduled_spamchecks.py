from django.core.management.base import BaseCommand
from django.utils import timezone
import pytz
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from settings.models import UserProfile, UserSettings
import asyncio
import aiohttp
import json
import random

class Command(BaseCommand):
    help = 'Launch scheduled spamchecks that are due'

    def __init__(self):
        super().__init__()
        # Create semaphores for rate limiting (10 requests per second)
        self.instantly_semaphore = asyncio.Semaphore(10)  # 10 concurrent requests for Instantly
        self.emailguard_semaphore = asyncio.Semaphore(10)  # 10 concurrent requests for EmailGuard

    async def make_instantly_request(self, session, method, url, **kwargs):
        """Make a rate-limited request to Instantly API"""
        async with self.instantly_semaphore:
            async with session.request(method, url, **kwargs) as response:
                return await response.json()

    async def make_emailguard_request(self, session, method, url, **kwargs):
        """Make a rate-limited request to EmailGuard API"""
        async with self.emailguard_semaphore:
            async with session.request(method, url, **kwargs) as response:
                if response.status not in [200, 201]:
                    raise Exception(f"EmailGuard API error: Status {response.status} - {await response.text()}")
                return await response.json()

    async def process_account(self, session, spamcheck, account, user_settings):
        """Process a single account for a spamcheck"""
        try:
            print(f"\n{'='*50}")
            print(f"Processing account: {account.email_account}")
            print(f"{'='*50}")

            # 1. Update email account sending limit
            print("\n[1/5] Updating email account sending limit...", flush=True)
            print("Calling Instantly API endpoint: POST https://app.instantly.ai/backend/api/v1/account/update/bulk")
            
            # Get user settings for authentication
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )
            
            update_limit_data = {
                "payload": {
                    "daily_limit": "100"  # Set daily limit to 100
                },
                "emails": [account.email_account]
            }
            
            update_limit_response = await self.make_instantly_request(
                session,
                'POST',
                "https://app.instantly.ai/backend/api/v1/account/update/bulk",
                headers={
                    "Cookie": f"__session={user_settings.instantly_user_token}",
                    "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                    "Content-Type": "application/json"
                },
                json=update_limit_data
            )
            
            if "error" in update_limit_response:
                raise Exception(f"Failed to update account limit: {update_limit_response['error']}")
                
            print("✓ Account sending limit updated successfully")

            # 2. Get emailguard tag and test emails
            print("\n[2/5] Getting EmailGuard tag...", flush=True)
            campaign_name = f"{spamcheck.name} - {account.email_account}"
            
            print("Calling EmailGuard API endpoint: POST https://app.emailguard.io/api/v1/inbox-placement-tests")
            emailguard_headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}",
                "Content-Type": "application/json"
            }
            
            emailguard_data = {
                "name": campaign_name,
                "type": "inbox_placement"
            }
            print(f"Request Headers: {emailguard_headers}")
            print(f"Request Data: {emailguard_data}")
            
            emailguard_data = await self.make_emailguard_request(
                session,
                'POST',
                "https://app.emailguard.io/api/v1/inbox-placement-tests",
                headers=emailguard_headers,
                json=emailguard_data
            )

            if "data" not in emailguard_data or "uuid" not in emailguard_data["data"]:
                raise Exception(f"EmailGuard response missing uuid: {emailguard_data}")
            
            emailguard_tag = emailguard_data["data"]["uuid"]
            test_emails = emailguard_data["data"]["inbox_placement_test_emails"]
            print(f"✓ Got EmailGuard tag (UUID): {emailguard_tag}")
            print(f"✓ Got {len(test_emails)} test email addresses")

            # 3. Create campaign with all settings
            print("\n[3/5] Creating campaign with settings...", flush=True)
            print("Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/campaigns")
            
            # Calculate schedule time based on campaign timezone
            campaign_tz = pytz.timezone('Etc/GMT+12')
            user_timezone = spamcheck.user.profile.timezone if hasattr(spamcheck.user, 'profile') else 'UTC'
            user_tz = pytz.timezone(user_timezone)
            
            # Get current time in user timezone
            current_time = timezone.localtime(timezone.now(), user_tz)
            
            # Convert to campaign timezone
            current_time_campaign_tz = current_time.astimezone(campaign_tz)
            
            # Round to nearest 30 minutes
            minutes = current_time_campaign_tz.minute
            if minutes < 30:
                start_minutes = "30"
                start_hour = str(current_time_campaign_tz.hour).zfill(2)
            else:
                start_minutes = "00"
                start_hour = str((current_time_campaign_tz.hour + 1) % 24).zfill(2)

            # Calculate end hour (1 hour after start)
            end_hour = str((int(start_hour) + 1) % 24).zfill(2)
            
            request_headers = {
                "Authorization": f"Bearer {spamcheck.user_organization.instantly_api_key}",
                "Content-Type": "application/json"
            }
            
            print(f"Scheduling campaign in Etc/GMT+12 timezone:")
            print(f"Start time: {start_hour}:{start_minutes}")
            print(f"End time: {end_hour}:{start_minutes}")
            
            request_data = {
                "name": campaign_name,
                "campaign_schedule": {
                    "schedules": [
                        {
                            "name": "Default Schedule",
                            "timing": {
                                "from": f"{start_hour}:{start_minutes}",
                                "to": f"{end_hour}:{start_minutes}"
                            },
                            "days": {
                                "0": True,  # Sunday
                                "1": True,  # Monday
                                "2": True,  # Tuesday
                                "3": True,  # Wednesday
                                "4": True,  # Thursday
                                "5": True,  # Friday
                                "6": True   # Saturday
                            },
                            "timezone": "Etc/GMT+12"
                        }
                    ]
                },
                "email_gap": 1,
                "text_only": spamcheck.options.text_only,
                "email_list": [account.email_account],
                "daily_limit": 100,
                "stop_on_reply": True,
                "stop_on_auto_reply": True,
                "link_tracking": spamcheck.options.link_tracking,
                "open_tracking": spamcheck.options.open_tracking,
                "sequences": [{
                    "steps": [{
                        "type": "email",
                        "variants": [{
                            "subject": spamcheck.options.subject,
                            "body": f"{spamcheck.options.body}\n\n{emailguard_tag}"
                        }]
                    }]
                }]
            }
            
            print(f"Request Headers: {request_headers}")
            print(f"Request Data: {request_data}")
                
            campaign_data = await self.make_instantly_request(
                session,
                'POST',
                "https://api.instantly.ai/api/v2/campaigns",
                headers=request_headers,
                json=request_data
            )
            
            print(f"Campaign creation response: {campaign_data}")
            
            if not campaign_data or not isinstance(campaign_data, dict):
                raise Exception(f"Invalid response from campaign creation: {campaign_data}")
            
            if 'id' not in campaign_data:
                raise Exception(f"Campaign ID not found in response: {campaign_data}")
            
            campaign_id = campaign_data["id"]
            print(f"✓ Campaign created with ID: {campaign_id}")
            
            # 4. Add leads
            print("\n[4/5] Adding leads...", flush=True)
            print("Calling Instantly API endpoint: POST https://app.instantly.ai/backend/api/v1/lead/add")
            
            leads_data = {
                "campaign_id": campaign_id,
                "skip_if_in_workspace": False,
                "skip_if_in_campaign": False,
                "leads": [{"email": email["email"]} for email in test_emails]
            }
            
            print(f"Adding {len(test_emails)} leads in bulk")
            
            leads_response = await self.make_instantly_request(
                session,
                'POST',
                "https://app.instantly.ai/backend/api/v1/lead/add",
                headers={
                    "Cookie": f"__session={user_settings.instantly_user_token}",
                    "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                    "Content-Type": "application/json"
                },
                json=leads_data
            )
            
            if "error" in leads_response:
                raise Exception(f"Failed to add leads: {leads_response['error']}")
                
            print(f"✓ Successfully added {len(test_emails)} leads in bulk")
            
            # 5. Launch campaign
            print("\n[5/5] Launching campaign...", flush=True)
            print(f"Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate")
            
            launch_response = await self.make_instantly_request(
                session,
                'POST',
                f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate",
                headers=request_headers,  # Reuse the Bearer token headers
                json={}  # Empty body required
            )
            
            if "error" in launch_response:
                raise Exception(f"Failed to launch campaign: {launch_response['error']}")
                
            print("✓ Campaign launched successfully")
            
            # Store campaign info after successful launch
            print("\nStoring campaign info in database...", flush=True)
            await asyncio.to_thread(
                UserSpamcheckCampaigns.objects.create,
                user=spamcheck.user,
                spamcheck=spamcheck,
                organization=spamcheck.user_organization,
                account_id=account,
                instantly_campaign_id=campaign_id,
                emailguard_tag=emailguard_tag
            )
            print("✓ Campaign info stored")
            
            print(f"\n{'='*50}")
            print(f"Account {account.email_account} processed successfully!")
            print(f"{'='*50}\n")
            
            return True
            
        except Exception as e:
            print(f"Error processing account {account.email_account}: {str(e)}")
            return False

    async def process_spamcheck(self, spamcheck):
        """Process a single spamcheck with all its accounts"""
        try:
            # Convert scheduled time to user's timezone
            user_timezone = spamcheck.user.profile.timezone if hasattr(spamcheck.user, 'profile') else 'UTC'
            user_tz = pytz.timezone(user_timezone)
            scheduled_time = timezone.localtime(spamcheck.scheduled_at, user_tz)
            current_time = timezone.localtime(timezone.now(), user_tz)

            self.stdout.write(f"\n{'-'*50}")
            self.stdout.write(f"Spamcheck ID: {spamcheck.id}")
            self.stdout.write(f"Name: {spamcheck.name}")
            self.stdout.write(f"User: {spamcheck.user.email}")
            self.stdout.write(f"User Timezone: {user_timezone}")
            self.stdout.write(f"Scheduled Time: {scheduled_time}")
            self.stdout.write(f"Current Time: {current_time}")
            self.stdout.write(f"Should Launch: {scheduled_time <= current_time}")
            self.stdout.write(f"Is Domain Based: {spamcheck.is_domain_based}")
            self.stdout.write(f"Options:")
            self.stdout.write(f"  - Subject: {spamcheck.options.subject if spamcheck.options else 'No options set'}")
            self.stdout.write(f"  - Open Tracking: {spamcheck.options.open_tracking if spamcheck.options else 'N/A'}")
            self.stdout.write(f"  - Link Tracking: {spamcheck.options.link_tracking if spamcheck.options else 'N/A'}")
            self.stdout.write(f"  - Text Only: {spamcheck.options.text_only if spamcheck.options else 'N/A'}")

            if scheduled_time <= current_time:
                self.stdout.write(self.style.SUCCESS(f"\nLaunching spamcheck {spamcheck.id} - {spamcheck.name}"))
                
                try:
                    # Set status to in_progress at the beginning
                    await asyncio.to_thread(
                        lambda: setattr(spamcheck, 'status', 'in_progress') or spamcheck.save()
                    )

                    # Get user settings
                    user_settings = await asyncio.to_thread(
                        UserSettings.objects.get,
                        user=spamcheck.user
                    )
                except UserSettings.DoesNotExist:
                    raise Exception("User settings not found. Please configure API keys first.")

                # Get accounts
                accounts = await asyncio.to_thread(lambda: list(spamcheck.accounts.all()))
                if not accounts:
                    raise Exception("No accounts found for this spamcheck.")

                # If domain-based, filter accounts to one per domain
                if spamcheck.is_domain_based:
                    self.stdout.write("\nFiltering accounts by domain...")
                    domain_accounts = {}
                    
                    # Group accounts by domain
                    domain_groups = {}
                    for account in accounts:
                        domain = account.email_account.split('@')[1]
                        if domain not in domain_groups:
                            domain_groups[domain] = []
                        domain_groups[domain].append(account)
                    
                    # Randomly select one account per domain
                    accounts = [random.choice(accounts_list) for accounts_list in domain_groups.values()]
                    
                    self.stdout.write(f"Selected {len(accounts)} accounts (one randomly per domain):")
                    for account in accounts:
                        self.stdout.write(f"  - {account.email_account}")

                # Process all accounts in parallel
                async with aiohttp.ClientSession() as session:
                    tasks = [
                        self.process_account(session, spamcheck, account, user_settings)
                        for account in accounts
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)

                # Check results
                if any(isinstance(result, Exception) for result in results):
                    # If any account failed, mark spamcheck as failed
                    await asyncio.to_thread(
                        lambda: setattr(spamcheck, 'status', 'failed') or spamcheck.save()
                    )
                    raise Exception("One or more accounts failed to process")
                # No need to set in_progress here since we did it at the beginning

            else:
                self.stdout.write(self.style.WARNING(f"Not yet time to launch spamcheck {spamcheck.id}"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))

    async def handle_async(self, *args, **options):
        now = timezone.now()
        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Checking for scheduled spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get all pending spamchecks
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                status='pending'
            ).select_related(
                'user',
                'user__profile',
                'options',
                'user_organization'
            ))
        )

        if not spamchecks:
            self.stdout.write(self.style.WARNING("No pending spamchecks found"))
            return

        self.stdout.write(f"Found {len(spamchecks)} pending spamchecks:\n")

        # Process all spamchecks in parallel
        await asyncio.gather(*[
            self.process_spamcheck(spamcheck)
            for spamcheck in spamchecks
        ])

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 