from django.core.management.base import BaseCommand
from django.utils import timezone
import pytz
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns
from settings.models import UserProfile, UserSettings
import asyncio
import aiohttp
import json

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

            # 1. Create instantly campaign
            print("\n[1/7] Creating campaign...", flush=True)
            campaign_name = f"{spamcheck.name} - {account.email_account}"
            
            request_data = {
                "name": campaign_name,
                "user_id": user_settings.instantly_user_id
            }
            request_headers = {
                "Cookie": f"__session={user_settings.instantly_user_token}",
                "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                "Content-Type": "application/json"
            }

            campaign_data = await self.make_instantly_request(
                session,
                'POST',
                "https://app.instantly.ai/backend/api/v1/campaign/create",
                headers=request_headers,
                json=request_data
            )
            campaign_id = campaign_data["id"]
            print(f"✓ Campaign created with ID: {campaign_id}")

            # 2. Configure campaign options
            print("\n[2/7] Configuring campaign options...", flush=True)
            options_data = {
                "campaignID": campaign_id,
                "orgID": spamcheck.user_organization.instantly_organization_id,
                "emailList": [account.email_account],
                "openTracking": spamcheck.options.open_tracking,
                "linkTracking": spamcheck.options.link_tracking,
                "textOnly": spamcheck.options.text_only,
                "dailyLimit": "50",
                "emailGap": 300,
                "stopOnReply": True,
                "stopOnAutoReply": True
            }

            options_data = await self.make_instantly_request(
                session,
                'POST',
                "https://app.instantly.ai/api/campaign/update/options",
                headers=request_headers,
                json=options_data
            )
            if "error" in options_data:
                raise Exception(f"Failed to configure campaign: {options_data['error']}")
            print("✓ Campaign options configured")

            # 3. Get emailguard tag
            print("\n[3/7] Getting EmailGuard tag...", flush=True)
            emailguard_headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}",
                "Content-Type": "application/json"
            }

            emailguard_data = {
                "name": campaign_name,
                "type": "inbox_placement"
            }

            emailguard_data = await self.make_emailguard_request(
                session,
                'POST',
                "https://app.emailguard.io/api/v1/inbox-placement-tests",
                headers=emailguard_headers,
                json=emailguard_data,
                timeout=aiohttp.ClientTimeout(total=30)
            )

            if "data" not in emailguard_data or "filter_phrase" not in emailguard_data["data"]:
                raise Exception(f"EmailGuard response missing filter_phrase: {emailguard_data}")

            emailguard_tag = emailguard_data["data"]["filter_phrase"]
            print(f"✓ Got EmailGuard tag: {emailguard_tag}")

            # 4. Add email sequence with emailguard tag
            print("\n[4/7] Adding email sequence...", flush=True)
            sequence_data = {
                "sequences": [{
                    "steps": [{
                        "type": "email",
                        "variants": [{
                            "subject": spamcheck.options.subject,
                            "body": f"{spamcheck.options.body}\n\n{emailguard_tag}"
                        }]
                    }]
                }],
                "campaignID": campaign_id,
                "orgID": spamcheck.user_organization.instantly_organization_id
            }

            sequence_data = await self.make_instantly_request(
                session,
                'POST',
                "https://app.instantly.ai/api/campaign/update/sequences",
                headers=request_headers,
                json=sequence_data
            )
            if "error" in sequence_data:
                raise Exception(f"Failed to add sequence: {sequence_data['error']}")
            print("✓ Email sequence added")

            # 5. Add leads (email accounts)
            print("\n[5/7] Adding leads...", flush=True)
            test_emails = [{"email": email["email"]} for email in emailguard_data["data"]["inbox_placement_test_emails"]]

            leads_data = {
                "api_key": spamcheck.user_organization.instantly_api_key,
                "campaign_id": campaign_id,
                "skip_if_in_workspace": False,
                "skip_if_in_campaign": False,
                "leads": test_emails
            }

            leads_data = await self.make_instantly_request(
                session,
                'POST',
                "https://api.instantly.ai/api/v1/lead/add",
                headers={"Content-Type": "application/json"},
                json=leads_data,
                timeout=aiohttp.ClientTimeout(total=30)
            )
            if "error" in leads_data:
                raise Exception(f"Failed to add leads: {leads_data['error']}")
            print(f"✓ Added {len(test_emails)} leads from EmailGuard")

            # 6. Set campaign schedule
            print("\n[6/7] Setting campaign schedule...", flush=True)
            detroit_tz = pytz.timezone('America/Detroit')
            detroit_time = timezone.localtime(timezone.now(), detroit_tz)
            minutes = detroit_time.minute

            if minutes < 30:
                start_minutes = "30"
                start_hour = str(detroit_time.hour).zfill(2)
            else:
                start_minutes = "00"
                start_hour = str((detroit_time.hour + 1) % 24).zfill(2)

            # Calculate end hour (1 hour after start)
            end_hour = str((int(start_hour) + 1) % 24).zfill(2)

            schedule_data = {
                "api_key": spamcheck.user_organization.instantly_api_key,
                "campaign_id": campaign_id,
                "start_date": detroit_time.strftime("%Y-%m-%d"),
                "end_date": "2029-06-08",
                "schedules": [
                    {
                        "name": "Everyday",
                        "days": {
                            "0": True,  # Sunday
                            "1": True,  # Monday
                            "2": True,  # Tuesday
                            "3": True,  # Wednesday
                            "4": True,  # Thursday
                            "5": True,  # Friday
                            "6": True   # Saturday
                        },
                        "timezone": "America/Detroit",
                        "timing": {
                            "from": f"{start_hour}:{start_minutes}",
                            "to": f"{end_hour}:{start_minutes}"  # End 1 hour after start
                        }
                    }
                ]
            }

            schedule_data = await self.make_instantly_request(
                session,
                'POST',
                "https://api.instantly.ai/api/v1/campaign/set/schedules",
                headers={"Content-Type": "application/json"},
                json=schedule_data
            )
            if "error" in schedule_data:
                raise Exception(f"Failed to set schedule: {schedule_data['error']}")
            print("✓ Campaign schedule set")

            # 7. Launch campaign immediately
            print("\n[7/7] Launching campaign...", flush=True)
            launch_data = {
                "api_key": spamcheck.user_organization.instantly_api_key,
                "campaign_id": campaign_id
            }

            launch_data = await self.make_instantly_request(
                session,
                'POST',
                "https://api.instantly.ai/api/v1/campaign/launch",
                headers={"Content-Type": "application/json"},
                json=launch_data
            )
            if "error" in launch_data:
                raise Exception(f"Failed to launch campaign: {launch_data['error']}")
            print("✓ Campaign launched")

            # Store campaign info
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
            self.stdout.write(f"Options:")
            self.stdout.write(f"  - Subject: {spamcheck.options.subject if spamcheck.options else 'No options set'}")
            self.stdout.write(f"  - Open Tracking: {spamcheck.options.open_tracking if spamcheck.options else 'N/A'}")
            self.stdout.write(f"  - Link Tracking: {spamcheck.options.link_tracking if spamcheck.options else 'N/A'}")
            self.stdout.write(f"  - Text Only: {spamcheck.options.text_only if spamcheck.options else 'N/A'}")

            if scheduled_time <= current_time:
                self.stdout.write(self.style.SUCCESS(f"\nLaunching spamcheck {spamcheck.id} - {spamcheck.name}"))
                
                try:
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
                elif all(results):
                    # If all accounts succeeded, mark spamcheck as in_progress
                    await asyncio.to_thread(
                        lambda: setattr(spamcheck, 'status', 'in_progress') or spamcheck.save()
                    )

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