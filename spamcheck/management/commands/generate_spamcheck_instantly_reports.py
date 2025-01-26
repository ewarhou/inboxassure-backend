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
import requests
import logging

class Command(BaseCommand):
    help = 'Generate reports for completed spamchecks'

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
        url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{emailguard_tag}"
        headers = {
            "Authorization": f"Bearer {emailguard_api_key}"
        }

        try:
            self.stdout.write(f"Making request to {url}")
            async with session.get(url, headers=headers) as response:
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

    def calculate_score(self, data, provider):
        """Calculate score for a specific provider"""
        self.stdout.write(f"\nCalculating score for {provider}:")
        
        if not data or 'inbox_placement_test_emails' not in data:
            self.stdout.write("  No data available")
            return 0.0
            
        emails = data['inbox_placement_test_emails']
        total_count = len(emails)
        
        if total_count == 0:
            self.stdout.write("  No emails found")
            return 0.0
            
        inbox_count = sum(1 for email in emails 
                         if email.get('provider') == provider 
                         and email.get('status') == 'received' 
                         and email.get('folder', '').lower() == 'inbox')
        
        self.stdout.write(f"  Total emails: {total_count}")
        self.stdout.write(f"  Inbox count: {inbox_count}")
        
        score = round(inbox_count / total_count, 2)
        self.stdout.write(f"  Final score: {score}")
        return score

    def parse_conditions(self, conditions_str):
        """Parse conditions string into structured format"""
        try:
            conditions = []
            parts = conditions_str.split('sending=')
            if len(parts) != 2:
                return None
                
            criteria, limits = parts
            sending_limits = limits.split('/')
            if len(sending_limits) != 2:
                return None
                
            daily_limit, hourly_limit = map(int, sending_limits)
            
            # Parse criteria
            if 'and' in criteria:
                subcriteria = criteria.split('and')
                op = 'and'
            elif 'or' in criteria:
                subcriteria = criteria.split('or')
                op = 'or'
            else:
                subcriteria = [criteria]
                op = 'single'
                
            for criterion in subcriteria:
                if '>=' in criterion:
                    provider, value = criterion.split('>=')
                    conditions.append({
                        'provider': provider.strip(),
                        'operator': '>=',
                        'value': float(value)
                    })
                    
            return {
                'operator': op,
                'conditions': conditions,
                'daily_limit': daily_limit,
                'hourly_limit': hourly_limit
            }
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error parsing conditions: {str(e)}"))
            return None

    def evaluate_conditions(self, spamcheck, google_score, outlook_score):
        """Evaluate conditions and update sending limits if met"""
        parsed = self.parse_conditions(spamcheck.conditions)
        if not parsed:
            self.stdout.write("  Invalid conditions format")
            return
            
        scores = {'google': google_score, 'outlook': outlook_score}
        conditions_met = []
        
        for condition in parsed['conditions']:
            provider = condition['provider']
            if provider not in scores:
                continue
                
            score = scores[provider]
            met = score >= condition['value']
            conditions_met.append(met)
            
        final_result = all(conditions_met) if parsed['operator'] == 'and' else any(conditions_met)
        
        if final_result:
            self.stdout.write("  Conditions met, updating sending limits")
            self.update_sending_limit(
                spamcheck.email_account.id,
                parsed['daily_limit'],
                parsed['hourly_limit']
            )
        else:
            self.stdout.write("  Conditions not met")

    def update_sending_limit(self, account_id, daily_limit, hourly_limit):
        """Update sending limit for an email account"""
        try:
            url = "https://app.instantly.ai/api/v1/account/update"
            headers = {
                "Content-Type": "application/json",
                "X-Access-Token": "your_access_token"
            }
            data = {
                "id": account_id,
                "sending_limit": {
                    "daily": daily_limit,
                    "hourly": hourly_limit
                }
            }
            
            self.stdout.write(f"  Updating sending limits - Daily: {daily_limit}, Hourly: {hourly_limit}")
            response = requests.post(url, headers=headers, json=data)
            
            if response.status_code == 200:
                self.stdout.write("  Sending limits updated successfully")
            else:
                self.stdout.write(self.style.ERROR(f"  Failed to update sending limits: {response.status_code}"))
                
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error updating sending limits: {str(e)}"))

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
                self.evaluate_conditions(spamcheck, google_score, outlook_score)

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
        self.stdout.write("\n=== Starting Report Generation ===")
        self.stdout.write(f"Current time: {timezone.now()}")

        # Get spamchecks that need reports
        spamchecks = UserSpamcheck.objects.filter(
            status='generating_reports'
        ).prefetch_related('campaigns')

        self.stdout.write(f"\nFound {spamchecks.count()} spamchecks in generating_reports status")

        for spamcheck in spamchecks:
            self.stdout.write(f"\nProcessing Spamcheck ID: {spamcheck.id}")
            self.stdout.write(f"Last updated: {spamcheck.updated_at}")
            
            # Calculate waiting time
            waiting_time = spamcheck.reports_waiting_time or 1.0  # Default 1 hour if not set
            self.stdout.write(f"Waiting time configured: {waiting_time} hours")
            
            time_since_update = (timezone.now() - spamcheck.updated_at).total_seconds() / 3600
            self.stdout.write(f"Time since last update: {time_since_update:.2f} hours")

            if time_since_update < waiting_time:
                self.stdout.write(f"Not enough time has passed. Skipping...")
                continue

            self.stdout.write("Enough time has passed. Processing campaigns...")
            
            # Process each campaign
            for campaign in spamcheck.campaigns.all():
                self.stdout.write(f"\n  Campaign ID: {campaign.id}")
                self.stdout.write(f"  EmailGuard Tag: {campaign.emailguard_tag}")
                
                try:
                    # Call EmailGuard API
                    url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{campaign.emailguard_tag}"
                    headers = {"Authorization": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6IjY1ZTg1ZjZhNjE2ZjY5MzJiZjc3ZjJiZiIsImlhdCI6MTcwOTc0NzA1MCwiZXhwIjoxNzQxMjgzMDUwfQ.Hs_YZZkKwxqLv7IHs6J8vZVIHGYBGWBtS6emyOhJr8k"}
                    
                    self.stdout.write("  Calling EmailGuard API...")
                    response = requests.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        self.stdout.write("  API call successful")
                        
                        # Calculate scores
                        google_score = self.calculate_score(data, 'gmail')
                        outlook_score = self.calculate_score(data, 'outlook')
                        
                        self.stdout.write(f"  Google Score: {google_score}")
                        self.stdout.write(f"  Outlook Score: {outlook_score}")
                        
                        # Create or update report
                        report, created = UserSpamcheckReport.objects.update_or_create(
                            spamcheck_instantly=spamcheck,
                            email_account=campaign.email_account,
                            defaults={
                                'google_pro_score': google_score,
                                'outlook_pro_score': outlook_score,
                                'report_link': f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}"
                            }
                        )
                        
                        self.stdout.write(f"  Report {'created' if created else 'updated'} successfully")
                        
                        # Update sending limits based on conditions
                        if spamcheck.conditions:
                            self.stdout.write(f"  Checking conditions: {spamcheck.conditions}")
                            self.evaluate_conditions(spamcheck, google_score, outlook_score)
                        else:
                            self.stdout.write("  No conditions specified, using default")
                            if google_score >= 0.5:
                                self.update_sending_limit(campaign.email_account.id, 25, 1)
                                self.stdout.write("  Applied default condition: google>=0.5sending=25/1")
                            
                    else:
                        self.stdout.write(self.style.ERROR(f"  API call failed with status {response.status_code}"))
                        
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Error processing campaign: {str(e)}"))

            # Update spamcheck status to completed
            spamcheck.status = 'completed'
            spamcheck.save()
            self.stdout.write(f"Spamcheck {spamcheck.id} marked as completed")

        self.stdout.write("\n=== Report Generation Complete ===") 