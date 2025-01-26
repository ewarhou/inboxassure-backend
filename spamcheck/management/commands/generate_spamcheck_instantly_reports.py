from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns, UserSpamcheckReport
from settings.models import UserSettings, UserInstantly
import aiohttp
import asyncio
from datetime import timedelta
import json
from asgiref.sync import sync_to_async
import re

class Command(BaseCommand):
    help = 'Generate reports for spamchecks in generating_reports status'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)  # Rate limit: 10 requests per second

    async def delete_instantly_campaign(self, session, campaign_id, instantly_api_key):
        """Delete campaign from Instantly"""
        url = f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}"
        headers = {
            "Authorization": f"Bearer {instantly_api_key}"
        }

        try:
            self.stdout.write(f"Deleting campaign {campaign_id} from Instantly")
            async with session.delete(url, headers=headers) as response:
                if response.status == 200:
                    self.stdout.write(self.style.SUCCESS(f"Successfully deleted campaign {campaign_id} from Instantly"))
                    return True
                else:
                    self.stdout.write(self.style.ERROR(f"Failed to delete campaign {campaign_id} from Instantly. Status: {response.status}"))
                    return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error deleting campaign {campaign_id} from Instantly: {str(e)}"))
            return False

    async def get_emailguard_report(self, session, emailguard_tag, emailguard_api_key):
        """Get report from EmailGuard API"""
        url = "https://n8n.findymail.app/webhook/emailguard"
        headers = {
            "Content-Type": "application/json"
        }
        data = {
            "URL": f"https://app.emailguard.io/api/v1/inbox-placement-tests/{emailguard_tag}",
            "Emailguard Token": emailguard_api_key
        }

        try:
            self.stdout.write(f"Making request to {url} with data: {data}")
            async with session.post(url, headers=headers, json=data) as response:
                if response.status != 200:
                    raise Exception(f"EmailGuard API error: {response.status}")
                
                response_text = await response.text()
                self.stdout.write(f"Raw API Response: {response_text}")
                
                try:
                    response_data = json.loads(response_text)
                    if isinstance(response_data, str):
                        self.stdout.write(self.style.ERROR(f"API returned string instead of JSON: {response_data}"))
                        return None
                    return response_data
                except json.JSONDecodeError as e:
                    self.stdout.write(self.style.ERROR(f"Failed to parse JSON response: {str(e)}"))
                    return None
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error getting EmailGuard report: {str(e)}"))
            return None

    def calculate_score(self, results, provider):
        """Calculate score for a specific provider"""
        if not results or not isinstance(results, list):
            self.stdout.write(self.style.ERROR(f"Invalid results format. Expected list, got: {type(results)}"))
            return 0.0

        # Filter results for the specific provider
        provider_results = [r for r in results if isinstance(r, dict) and r.get('provider') == provider]
        if not provider_results:
            self.stdout.write(f"No results found for provider: {provider}")
            return 0.0

        total_count = len(provider_results)
        
        # Count emails in inbox, only if they are received
        inbox_count = sum(1 for r in provider_results if (
            isinstance(r, dict) and 
            r.get('status') == 'email_received' and  # Only count if email is received
            r.get('folder', '').lower() == 'inbox'
        ))
        
        # Log detailed information
        self.stdout.write(f"\nProvider {provider} details:")
        self.stdout.write(f"- Total emails: {total_count}")
        self.stdout.write(f"- Emails in inbox: {inbox_count}")
        self.stdout.write("- Status breakdown:")
        status_count = {}
        for r in provider_results:
            status = r.get('status', 'unknown')
            folder = r.get('folder', 'unknown')
            key = f"{status} - {folder}"
            status_count[key] = status_count.get(key, 0) + 1
        for status, count in status_count.items():
            self.stdout.write(f"  • {status}: {count}")
        
        # Calculate score (0-1) with 2 decimal places
        # Non-received emails are counted as not in inbox
        score = inbox_count / total_count if total_count > 0 else 0.0
        rounded_score = round(score, 2)
        
        self.stdout.write(f"Final score: {inbox_count}/{total_count} = {rounded_score}")
        return rounded_score

    def parse_conditions(self, conditions_str):
        """Parse conditions string into structured format"""
        if not conditions_str:
            # Default condition: google>=0.5sending=25/1
            return {
                'conditions': [('google', '>=', 0.5)],
                'sending': (25, 1)
            }

        # Split into conditions and sending parts
        parts = conditions_str.split('sending=')
        if len(parts) != 2:
            self.stdout.write(self.style.ERROR(f"Invalid conditions format: {conditions_str}"))
            return None

        conditions_part, sending_part = parts

        # Parse sending limits
        try:
            true_limit, false_limit = map(int, sending_part.split('/'))
        except ValueError:
            self.stdout.write(self.style.ERROR(f"Invalid sending limits format: {sending_part}"))
            return None

        # Parse conditions
        conditions = []
        condition_parts = conditions_part.split('and') if 'and' in conditions_part else conditions_part.split('or')
        operator = 'and' if 'and' in conditions_part else 'or'

        pattern = r'(google|outlook)(>=|<=|>|<|=|!=)(\d+\.?\d*)'
        for part in condition_parts:
            match = re.match(pattern, part.strip())
            if not match:
                self.stdout.write(self.style.ERROR(f"Invalid condition format: {part}"))
                return None
            
            metric, comparison, value = match.groups()
            conditions.append((metric, comparison, float(value)))

        return {
            'conditions': conditions,
            'operator': operator,
            'sending': (true_limit, false_limit)
        }

    def evaluate_conditions(self, conditions_data, google_score, outlook_score):
        """Evaluate conditions and return appropriate sending limit"""
        if not conditions_data:
            return 1  # Default to minimum sending limit if conditions are invalid

        conditions = conditions_data['conditions']
        operator = conditions_data.get('operator', 'and')
        true_limit, false_limit = conditions_data['sending']

        def evaluate_single_condition(condition):
            metric, comparison, value = condition
            score = google_score if metric == 'google' else outlook_score

            if comparison == '>=':
                return score >= value
            elif comparison == '<=':
                return score <= value
            elif comparison == '>':
                return score > value
            elif comparison == '<':
                return score < value
            elif comparison == '=':
                return score == value
            elif comparison == '!=':
                return score != value
            return False

        results = [evaluate_single_condition(condition) for condition in conditions]
        final_result = all(results) if operator == 'and' else any(results)
        
        return true_limit if final_result else false_limit

    async def update_sending_limit(self, session, email, limit, user_settings, organization_token):
        """Update sending limit for an email account"""
        url = "https://app.instantly.ai/backend/api/v1/account/update/bulk"
        headers = {
            "Cookie": f"__session={user_settings.instantly_user_token}",
            "X-Org-Auth": organization_token,
            "Content-Type": "application/json"
        }
        data = {
            "payload": {
                "daily_limit": str(limit)
            },
            "emails": [email]
        }

        try:
            async with session.post(url, headers=headers, json=data) as response:
                if response.status == 200:
                    self.stdout.write(self.style.SUCCESS(f"Updated sending limit for {email} to {limit}"))
                    return True
                else:
                    self.stdout.write(self.style.ERROR(f"Failed to update sending limit for {email}. Status: {response.status}"))
                    return False
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error updating sending limit for {email}: {str(e)}"))
            return False

    async def process_campaign(self, session, campaign, user_settings):
        """Process a single campaign and generate report"""
        try:
            self.stdout.write(f"\nProcessing campaign {campaign.id} with tag {campaign.emailguard_tag}")
            
            # Get report from EmailGuard
            report_data = await self.get_emailguard_report(
                session,
                campaign.emailguard_tag,
                user_settings.emailguard_api_key
            )
            
            if not report_data:
                self.stdout.write(self.style.ERROR(f"No report data for campaign {campaign.id}"))
                return False

            # Check response structure
            if not isinstance(report_data, dict):
                self.stdout.write(self.style.ERROR(f"Invalid response format. Expected dict, got: {type(report_data)}"))
                return False

            # Get results from the correct path in response
            data = report_data.get('data', {})
            if not isinstance(data, dict):
                self.stdout.write(self.style.ERROR(f"Invalid data format. Expected dict, got: {type(data)}"))
                return False

            results = data.get('inbox_placement_test_emails', [])
            if not isinstance(results, list):
                self.stdout.write(self.style.ERROR(f"Invalid test emails format. Expected list, got: {type(results)}"))
                return False

            # Calculate scores
            google_score = self.calculate_score(results, 'Google')
            outlook_score = self.calculate_score(results, 'Microsoft')

            self.stdout.write(f"Scores calculated:")
            self.stdout.write(f"- Google Pro Score: {google_score}")
            self.stdout.write(f"- Outlook Pro Score: {outlook_score}")

            # Get required properties using sync_to_async
            get_organization = sync_to_async(lambda: campaign.organization)()
            get_email_account = sync_to_async(lambda: campaign.account_id.email_account)()
            get_spamcheck = sync_to_async(lambda: campaign.spamcheck)()
            
            organization = await get_organization
            email_account = await get_email_account
            spamcheck = await get_spamcheck

            # Create report using sync_to_async
            create_report = sync_to_async(UserSpamcheckReport.objects.create)
            report = await create_report(
                organization=organization,
                email_account=email_account,
                report_link=f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}",
                google_pro_score=google_score,
                outlook_pro_score=outlook_score,
                spamcheck_instantly=spamcheck
            )

            self.stdout.write(self.style.SUCCESS(f"Created report {report.id} for campaign {campaign.id}"))

            # Parse conditions and update sending limit
            conditions_data = self.parse_conditions(spamcheck.conditions)
            if conditions_data:
                sending_limit = self.evaluate_conditions(conditions_data, google_score, outlook_score)
                self.stdout.write(f"Evaluated conditions - setting sending limit to: {sending_limit}")
                
                # Update sending limit in Instantly
                await self.update_sending_limit(
                    session,
                    email_account,
                    sending_limit,
                    user_settings,
                    organization.instantly_organization_token
                )

            # Get user and organization_id using sync_to_async
            get_user = sync_to_async(lambda: campaign.spamcheck.user)()
            get_org_id = sync_to_async(lambda: campaign.organization.instantly_organization_id)()
            
            user = await get_user
            org_id = await get_org_id

            # Get instantly API key from UserInstantly model using sync_to_async
            get_user_instantly = sync_to_async(UserInstantly.objects.get)
            user_instantly = await get_user_instantly(
                user=user,
                instantly_organization_id=org_id
            )

            # Delete campaign from Instantly
            if await self.delete_instantly_campaign(session, campaign.instantly_campaign_id, user_instantly.instantly_api_key):
                # Update campaign status to deleted using sync_to_async
                update_campaign = sync_to_async(lambda: setattr(campaign, 'campaign_status', 'deleted') or campaign.save())
                await update_campaign()
                self.stdout.write(self.style.SUCCESS(f"Campaign {campaign.id} status updated to deleted"))
            else:
                self.stdout.write(self.style.ERROR(f"Failed to delete campaign {campaign.id} from Instantly"))

            return True

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing campaign {campaign.id}: {str(e)}"))
            self.stdout.write(self.style.ERROR(f"Full error details: {e.__class__.__name__}: {str(e)}"))
            return False

    async def process_spamcheck(self, session, spamcheck):
        """Process a single spamcheck and all its completed campaigns"""
        try:
            self.stdout.write(f"\nProcessing spamcheck {spamcheck.id}")
            
            # Get user settings
            user_settings = await asyncio.to_thread(
                UserSettings.objects.get,
                user=spamcheck.user
            )

            # Get all completed campaigns
            campaigns = await asyncio.to_thread(
                lambda: list(UserSpamcheckCampaigns.objects.filter(
                    spamcheck=spamcheck,
                    campaign_status='completed'
                ).select_related('organization', 'account_id', 'spamcheck'))
            )

            if not campaigns:
                self.stdout.write(f"No completed campaigns found for spamcheck {spamcheck.id}")
                return False

            # Process all campaigns
            results = await asyncio.gather(*[
                self.process_campaign(session, campaign, user_settings)
                for campaign in campaigns
            ])

            # If all campaigns processed successfully, mark spamcheck as completed
            if all(results):
                await asyncio.to_thread(
                    lambda: setattr(spamcheck, 'status', 'completed') or spamcheck.save()
                )
                self.stdout.write(self.style.SUCCESS(f"Spamcheck {spamcheck.id} marked as completed"))
                return True
            else:
                self.stdout.write(self.style.ERROR(f"Some campaigns failed for spamcheck {spamcheck.id}"))
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()

        self.stdout.write(f"\n{'='*50}")
        self.stdout.write(f"Generating reports for spamchecks at {now}")
        self.stdout.write(f"{'='*50}\n")

        # Get spamchecks that need report generation
        spamchecks = await asyncio.to_thread(
            lambda: list(UserSpamcheck.objects.filter(
                status='generating_reports'
            ).select_related('user'))
        )

        if not spamchecks:
            self.stdout.write(self.style.WARNING("No spamchecks need report generation"))
            return

        self.stdout.write(f"Found {len(spamchecks)} spamchecks to process")

        # Process all spamchecks
        async with aiohttp.ClientSession() as session:
            results = await asyncio.gather(*[
                self.process_spamcheck(session, spamcheck)
                for spamcheck in spamchecks
            ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\nProcessed {len(spamchecks)} spamchecks:")
        self.stdout.write(f"- Successful: {success_count}")
        self.stdout.write(f"- Failed: {len(spamchecks) - success_count}")

    def handle(self, *args, **options):
        """Entry point for the command"""
        try:
            # Get spamchecks that need reports generated
            spamchecks = UserSpamcheck.objects.filter(
                status='generating_reports'
            ).exclude(
                Q(updated_at__gte=timezone.now() - timezone.timedelta(minutes=5))  # Skip if updated in last 5 minutes
            )

            for spamcheck in spamchecks:
                # Get waiting time in hours (default 1 hour if not set)
                waiting_time = spamcheck.reports_waiting_time or 1.0
                
                # Check if enough time has passed since last update
                time_threshold = timezone.now() - timezone.timedelta(hours=waiting_time)
                if spamcheck.updated_at > time_threshold:
                    self.stdout.write(f"Skipping spamcheck {spamcheck.id} - Not enough time passed (waiting {waiting_time}h)")
                    continue

                self.stdout.write(f"Processing spamcheck {spamcheck.id}")
                asyncio.run(self.handle_async(*args, **options))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error processing spamchecks: {str(e)}")) 