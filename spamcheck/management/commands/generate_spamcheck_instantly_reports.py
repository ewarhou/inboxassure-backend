"""
This command generates comprehensive reports for completed spamcheck campaigns.

Purpose:
- Finds spamchecks in 'generating_reports' status
- Respects waiting time before generating reports (based on spamcheck's updated_at time):
  * 0 hours: Generate immediately
  * 0.5 hours: Wait 30 minutes after last update
  * 1 hour: Wait 1 hour after last update (default)
  * Any custom hours value (2h, 3h, 4h, 5h, 6h)
- Gets report data from EmailGuard API
- Creates/updates reports in database with:
  * Google and Outlook scores
  * Account status (good/bad)
  * Used email subject and body
  * Account tags (as UUID list)
  * Sending limits based on conditions
  * Workspace UUIDs (Instantly and Bison)
- Updates account sending limits based on results
- Handles domain-based spamchecks
- Deletes completed campaigns from Instantly to clean up resources

Flow:
1. Finds spamchecks ready for report generation:
   - Status must be 'generating_reports'
   - Must satisfy waiting time condition:
     * No waiting time specified (uses default 1h)
     * Waiting time is 0 (immediate)
     * Enough time has passed since last update (updated_at + waiting_time <= now)

2. For each spamcheck:
   - Gets EmailGuard report data
   - Calculates scores
   - Fetches account tags from Instantly API
   - Creates comprehensive reports with:
     * Email scores and status
     * Campaign content (subject/body)
     * Account tags
     * Sending limits
     * Workspace identifiers
   - Updates sending limits based on conditions:
     * Format: google>0.5andoutlook>0.3sending=23/3
     * Example: If Google score > 0.5 AND Outlook score > 0.3:
       - Set sending limit to 23 for good accounts
       - Set sending limit to 3 for bad accounts
   - Handles domain-based reporting
   - Deletes campaign from Instantly using campaign ID
   - Marks campaign as 'deleted' in database

3. Marks spamchecks as completed when done

Features:
- Async/await for better performance
- Rate limiting
- Error handling
- Domain-based reporting
- Bulk operations
- Campaign cleanup
- Automatic deletion of completed campaigns
- Configurable waiting time
- Account tag tracking
- Comprehensive reporting
- Cross-platform workspace tracking

API Integrations:
1. EmailGuard Report: GET https://app.emailguard.io/api/v1/inbox-placement-tests/{tag}
2. Instantly Account List: POST https://app.instantly.ai/api/v1/account/list
3. Instantly Update Account: POST https://app.instantly.ai/backend/api/v1/account/update/bulk
4. Instantly Delete Campaign: DELETE https://api.instantly.ai/api/v2/campaigns/{id}
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, F
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns, UserSpamcheckReport
from settings.models import UserSettings
from asgiref.sync import sync_to_async  # For safe DB calls in async context
import aiohttp
import asyncio
from datetime import timedelta
import json
from collections import defaultdict
import requests
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Generate reports for completed spamcheck campaigns'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)
        self.conditions_met = False

    def calculate_score(self, data, provider):
        """Calculate provider score from EmailGuard data"""
        if not data or 'data' not in data or 'inbox_placement_test_emails' not in data['data']:
            return 0.0

        emails = data['data']['inbox_placement_test_emails']
        provider_emails = [e for e in emails if e['provider'] == provider]
        
        if not provider_emails:
            return 0.0

        # Count emails in inbox, safely handling None values
        inbox_count = sum(1 for e in provider_emails if e.get('folder') and e.get('folder', '').lower() == 'inbox')
        
        self.stdout.write(f"\nCalculating score for {provider}:")
        self.stdout.write(f"  Total {provider} emails: {len(provider_emails)}")
        self.stdout.write(f"  Emails in inbox: {inbox_count}")
        
        score = inbox_count / len(provider_emails)
        self.stdout.write(f"  Score: {score} ({inbox_count}/{len(provider_emails)})")
        
        return score

    def parse_conditions(self, conditions_str):
        """Parse conditions string into dict
        Format: google>0.5andoutlook>0.3sending=23/3
        Returns: {
            'google': 0.5,
            'outlook': 0.3,
            'good_limit': 23,  # sending limit for accounts that meet conditions
            'bad_limit': 3     # sending limit for accounts that don't meet conditions
        }
        """
        if not conditions_str:
            return {'google': 0.5, 'outlook': 0.5, 'good_limit': 25, 'bad_limit': 3}

        conditions = {}
        
        # First split by 'sending=' to separate scores and limits
        parts = conditions_str.lower().split('sending=')
        if len(parts) == 2:
            scores_part = parts[0]
            limits_part = parts[1]
            
            # Parse scores
            score_parts = scores_part.split('and')
            for part in score_parts:
                if 'google>' in part:
                    conditions['google'] = float(part.split('>')[1])
                elif 'outlook>' in part:
                    conditions['outlook'] = float(part.split('>')[1])
            
            # Parse sending limits
            if '/' in limits_part:
                good_limit, bad_limit = limits_part.split('/')
                conditions['good_limit'] = int(good_limit)
                conditions['bad_limit'] = int(bad_limit)
            else:
                conditions['good_limit'] = int(limits_part)
                conditions['bad_limit'] = 3
        
        # Set defaults for any missing values
        if 'google' not in conditions:
            conditions['google'] = 0.5
        if 'outlook' not in conditions:
            conditions['outlook'] = 0.5
        if 'good_limit' not in conditions:
            conditions['good_limit'] = 25
        if 'bad_limit' not in conditions:
            conditions['bad_limit'] = 3

        return conditions

    def evaluate_conditions(self, spamcheck, google_score, outlook_score, email):
        """Evaluate if scores meet conditions and return appropriate sending limit"""
        self.stdout.write(f"\nEvaluating conditions from database:")
        self.stdout.write(f"  Raw conditions string: {spamcheck.conditions}")
        
        conditions = self.parse_conditions(spamcheck.conditions)
        self.stdout.write(f"  Parsed conditions: {json.dumps(conditions, indent=2)}")
        
        # Check if scores meet thresholds
        google_ok = google_score >= conditions.get('google', 0.5)
        outlook_ok = outlook_score >= conditions.get('outlook', 0.5)
        
        self.conditions_met = google_ok and outlook_ok
        
        # Return appropriate sending limit
        if self.conditions_met:
            sending_limit = conditions.get('good_limit', 25)
            self.stdout.write(f"  ✓ Conditions met - Using good limit: {sending_limit}")
            return True, sending_limit
        else:
            sending_limit = conditions.get('bad_limit', 3)
            self.stdout.write(f"  ✗ Conditions not met - Using bad limit: {sending_limit}")
            return False, sending_limit

    def evaluate_default_condition(self, google_score, outlook_score):
        """Evaluate default condition (google >= 0.5)"""
        return google_score >= 0.5

    async def update_sending_limit(self, organization, emails, daily_limit):
        """Update sending limit for email accounts"""
        if not isinstance(emails, list):
            emails = [emails]
            
        try:
            # Wrap the DB call safely in an async context
            user_settings = await sync_to_async(UserSettings.objects.get, thread_sensitive=True)(user=organization.user)
            
            headers = {
                "Cookie": f"__session={user_settings.instantly_user_token}",
                "X-Org-Auth": organization.instantly_organization_token,
                "Content-Type": "application/json"
            }
            
            data = {
                "payload": {
                    "daily_limit": str(daily_limit)
                },
                "emails": emails
            }
            
            response = await asyncio.to_thread(
                requests.post,
                "https://app.instantly.ai/backend/api/v1/account/update/bulk",
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                self.stdout.write(f"  ✓ Updated sending limit to {daily_limit} for {len(emails)} account(s)")
            else:
                self.stdout.write(self.style.ERROR(f"  ✗ Failed to update sending limit: {response.text}"))
                
        except UserSettings.DoesNotExist:
            self.stdout.write(self.style.ERROR("  ✗ User settings not found"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Error updating sending limit: {str(e)}"))

    async def get_account_tags(self, session, email, organization, user_settings):
        """Get tags for a specific email account"""
        try:
            url = "https://app.instantly.ai/backend/api/v1/account/list"
            headers = {
                "Cookie": f"__session={user_settings.instantly_user_token}",
                "X-Org-Auth": organization.instantly_organization_token,
                "Content-Type": "application/json"
            }
            
            data = {
                "search": email,
                "limit": 1,
                "include_tags": True
            }
            
            self.stdout.write(f"  Fetching tags for email: {email}")
            response = await asyncio.to_thread(
                requests.post,
                url,
                headers=headers,
                json=data
            )
            
            if response.status_code == 200:
                response_data = response.json()
                self.stdout.write(f"  Raw Account Response: {json.dumps(response_data, indent=2)}")
                
                accounts = response_data.get('accounts', [])
                if accounts:
                    account = accounts[0]
                    tags = account.get('tags', [])
                    
                    # Extract tag information
                    tag_info = [
                        {
                            'id': tag['id'],
                            'label': tag['label']
                        } for tag in tags
                    ]
                    
                    # Log found tags
                    self.stdout.write("  Found tags:")
                    for tag in tag_info:
                        self.stdout.write(f"    - {tag['label']} (ID: {tag['id']})")
                    
                    # Return only the UUIDs
                    return [tag['id'] for tag in tags]
                else:
                    self.stdout.write("  No account found with this email")
            else:
                self.stdout.write(self.style.ERROR(f"  API call failed: {response.status_code} - {response.text}"))
            
            return []
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error fetching account tags: {str(e)}"))
            return []

    def create_report_sync(self, campaign, google_score, outlook_score, is_good, sending_limit, tags_uuid_list):
        """Synchronous function to create/update report with additional fields"""
        return UserSpamcheckReport.objects.update_or_create(
            spamcheck_instantly=campaign.spamcheck,
            email_account=campaign.account_id.email_account,
            defaults={
                'organization': campaign.organization,
                'google_pro_score': google_score,
                'outlook_pro_score': outlook_score,
                'report_link': f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}",
                'is_good': is_good,
                'used_subject': campaign.spamcheck.options.subject if campaign.spamcheck.options else None,
                'used_body': campaign.spamcheck.options.body if campaign.spamcheck.options else None,
                'sending_limit': sending_limit,
                'tags_uuid_list': ','.join(tags_uuid_list) if tags_uuid_list else None,
                'instantly_workspace_uuid': campaign.organization.instantly_organization_id,  # This is the UUID
                'bison_workspace_uuid': campaign.organization.bison_organization_api_key if hasattr(campaign.organization, 'bison_organization_api_key') else None
            }
        )

    @sync_to_async
    def get_user_settings(self, user):
        """Get user settings synchronously"""
        return UserSettings.objects.get(user=user)

    @sync_to_async
    def get_campaigns(self, spamcheck):
        """Get campaigns with prefetched related data"""
        return list(
            UserSpamcheckCampaigns.objects.filter(spamcheck=spamcheck)
            .select_related('account_id', 'spamcheck', 'organization', 'organization__user')
            .prefetch_related('spamcheck__options')
        )

    @sync_to_async
    def save_campaign_status(self, campaign, status):
        """Save campaign status synchronously"""
        campaign.campaign_status = status
        campaign.save()

    async def delete_instantly_campaign(self, session, campaign_id, instantly_api_key):
        """Delete campaign from Instantly with improved error logging"""
        url = f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}"
        headers = {
            "Authorization": f"Bearer {instantly_api_key}"
        }

        try:
            async with self.rate_limit:
                async with session.delete(url, headers=headers) as response:
                    if response.status == 200:
                        self.stdout.write(f"  ✓ Successfully deleted campaign {campaign_id} from Instantly")
                        return True
                    else:
                        error_detail = await response.text()
                        self.stdout.write(self.style.ERROR(
                            f"  ✗ Failed to delete campaign {campaign_id}: {response.status} - {error_detail}"
                        ))
                        return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  ✗ Error deleting campaign {campaign_id}: {str(e)}"))
            return False

    async def process_campaign(self, session, campaign, user_settings):
        """Process a single campaign and generate report with additional data"""
        try:
            self.stdout.write(f"\n  Campaign ID: {campaign.instantly_campaign_id}")
            self.stdout.write(f"  EmailGuard Tag: {campaign.emailguard_tag}")
            
            # Get EmailGuard report
            url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{campaign.emailguard_tag}"
            headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}"
            }
            
            self.stdout.write("  Calling EmailGuard API...")
            response = await asyncio.to_thread(requests.get, url, headers=headers)
            
            if response.status_code == 200:
                self.stdout.write("  API call successful")
                data = response.json()
                self.stdout.write(f"  Raw EmailGuard Response: {json.dumps(data, indent=2)}")

                # Calculate scores
                google_score = self.calculate_score(data, 'Google')
                outlook_score = self.calculate_score(data, 'Microsoft')

                self.stdout.write(f"  Google Score: {google_score}")
                self.stdout.write(f"  Outlook Score: {outlook_score}")

                # Evaluate conditions and get appropriate sending limit
                is_good, sending_limit = self.evaluate_conditions(campaign.spamcheck, google_score, outlook_score, campaign.account_id.email_account)

                # Get account tags
                self.stdout.write("  Fetching account tags...")
                tags_uuid_list = await self.get_account_tags(
                    session,
                    campaign.account_id.email_account,
                    campaign.organization,
                    user_settings
                )
                self.stdout.write(f"  Found {len(tags_uuid_list)} tags")

                # Create or update report using the sync function
                report = await asyncio.to_thread(
                    self.create_report_sync,
                    campaign,
                    google_score,
                    outlook_score,
                    is_good,
                    sending_limit,
                    tags_uuid_list
                )

                self.stdout.write(f"  Report {'created' if report[1] else 'updated'} successfully")
                self.stdout.write(f"  Account status: {'✓ Good' if is_good else '✗ Bad'}")
                self.stdout.write(f"  Sending limit set to: {sending_limit}")
                self.stdout.write(f"  Tags stored: {tags_uuid_list}")

                # Update sending limit based on conditions
                await self.update_sending_limit(campaign.organization, campaign.account_id.email_account, sending_limit)

                # Delete campaign from Instantly
                self.stdout.write("\n  Deleting campaign from Instantly...")
                self.stdout.write(f"  Using Instantly Campaign ID: {campaign.instantly_campaign_id}")
                deleted = await self.delete_instantly_campaign(
                    session, 
                    campaign.instantly_campaign_id,
                    campaign.organization.instantly_api_key
                )
                
                if deleted:
                    await self.save_campaign_status(campaign, 'deleted')
                    self.stdout.write("  ✓ Campaign marked as deleted in database")

                return True
            else:
                self.stdout.write(self.style.ERROR(
                    f"  API call failed:\n"
                    f"    Status: {response.status_code}\n"
                    f"    Response: {response.text}"
                ))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error processing campaign: {str(e)}"))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck"""
        try:
            self.stdout.write(f"\nProcessing Spamcheck ID: {spamcheck.id}")
            
            # Get user settings using the async helper
            try:
                user_settings = await self.get_user_settings(spamcheck.user)
            except UserSettings.DoesNotExist:
                self.stdout.write(self.style.ERROR("User settings not found"))
                return False

            # Get campaigns with related data prefetched
            campaigns = await self.get_campaigns(spamcheck)

            if not campaigns:
                self.stdout.write("No campaigns found")
                return False

            # Process all campaigns
            results = await asyncio.gather(*[
                self.process_campaign(session, campaign, user_settings)
                for campaign in campaigns
            ])

            # Only mark spamcheck as completed if all campaigns were processed successfully
            if all(results):
                spamcheck.status = 'completed'
                await asyncio.to_thread(spamcheck.save)
                self.stdout.write(self.style.SUCCESS(f"All reports generated successfully. Spamcheck {spamcheck.id} marked as completed"))
                return True
            else:
                self.stdout.write(self.style.WARNING(f"Some reports failed to generate for spamcheck {spamcheck.id}. Status remains as generating_reports"))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def get_ready_spamchecks(self):
        """Get spamchecks that are ready for report generation based on waiting time"""
        now = timezone.now()
        
        return await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                Q(status='generating_reports') &
                (
                    Q(reports_waiting_time__isnull=True) |  # No waiting time specified (uses default 1h)
                    Q(reports_waiting_time=0) |  # Immediate generation
                    Q(updated_at__lte=now - timedelta(hours=1), reports_waiting_time=1.0) |  # Default 1h waiting
                    Q(updated_at__lte=now - timedelta(minutes=30), reports_waiting_time=0.5) |  # 30min waiting
                    Q(updated_at__lte=now - timedelta(hours=2), reports_waiting_time=2.0) |  # 2h waiting
                    Q(updated_at__lte=now - timedelta(hours=3), reports_waiting_time=3.0) |  # 3h waiting
                    Q(updated_at__lte=now - timedelta(hours=4), reports_waiting_time=4.0) |  # 4h waiting
                    Q(updated_at__lte=now - timedelta(hours=5), reports_waiting_time=5.0) |  # 5h waiting
                    Q(updated_at__lte=now - timedelta(hours=6), reports_waiting_time=6.0)   # 6h waiting
                )
            ).select_related('user', 'user_organization'))
        )

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n=== Starting Report Generation ===")
        self.stdout.write(f"Current time: {now}")
        
        self.stdout.write("\nAPI Endpoints being used:")
        self.stdout.write("1. EmailGuard Report: GET https://app.emailguard.io/api/v1/inbox-placement-tests/{tag}")
        self.stdout.write("2. Instantly Update Account: POST https://app.instantly.ai/backend/api/v1/account/update/bulk")
        self.stdout.write("3. Instantly Delete Campaign: DELETE https://api.instantly.ai/api/v2/campaigns/{id}")
        self.stdout.write("---\n")

        # Get spamchecks ready for report generation based on waiting time
        spamchecks = await self.get_ready_spamchecks()

        if not spamchecks:
            self.stdout.write("No spamchecks ready for report generation")
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks ready for report generation\n")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\n=== Report Generation Complete ===")
        self.stdout.write(f"Successfully processed {success_count} of {len(spamchecks)} spamchecks")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options))