from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q
from spamcheck.models import UserSpamcheck, UserSpamcheckCampaigns, UserSpamcheckReport, UserSpamcheckAccounts
from settings.models import UserSettings, UserInstantly
import aiohttp
import asyncio
from datetime import timedelta
import json
from asgiref.sync import sync_to_async
import re
import requests
import logging
from collections import defaultdict

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
        try:
            self.stdout.write(f"\nCalculating score for {provider}:")
            
            # Get emails from the correct path in response
            emails = data['data']['inbox_placement_test_emails']
            
            # Filter emails for the specific provider
            provider_emails = [
                email for email in emails 
                if email['provider'] == provider and email['status'] == 'email_received'
            ]
            
            total_emails = len([e for e in emails if e['provider'] == provider])
            inbox_emails = len([e for e in provider_emails if e['folder'] == 'inbox'])
            
            self.stdout.write(f"  Total {provider} emails: {total_emails}")
            self.stdout.write(f"  Emails in inbox: {inbox_emails}")
            
            if total_emails == 0:
                self.stdout.write(f"  No emails found for {provider}")
                return 0.0
                
            score = round(inbox_emails / total_emails, 2)
            self.stdout.write(f"  Score: {score} ({inbox_emails}/{total_emails})")
            return score
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"  Error calculating {provider} score: {str(e)}"))
            return 0.0

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

    def evaluate_conditions(self, spamcheck, google_score, outlook_score, email_account):
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
        self.conditions_met = final_result  # Store the result
        
        if final_result:
            self.stdout.write("  Conditions met, updating sending limits")
            self.update_sending_limit(
                spamcheck.user_organization,  # First argument should be user_organization
                email_account,                # Second argument is email_account
                parsed['daily_limit']         # Third argument is daily_limit
            )
        else:
            self.stdout.write("  Conditions not met")

    def update_sending_limit(self, user_organization, email_accounts, daily_limit):
        """Update sending limit for email accounts."""
        url = "https://app.instantly.ai/backend/api/v1/account/update/bulk"
        
        # Get user settings to get the session token
        user_settings = UserSettings.objects.get(user=user_organization.user)
        
        headers = {
            "Cookie": f"__session={user_settings.instantly_user_token}",
            "X-Org-Auth": user_organization.instantly_organization_token,
            "X-Org-Id": user_organization.instantly_organization_id,
            "Content-Type": "application/json"
        }
        
        # Handle both single email and list of emails
        emails = [email_accounts] if isinstance(email_accounts, str) else email_accounts
        
        data = {
            "payload": {
                "daily_limit": str(daily_limit)
            },
            "emails": emails
        }
        
        self.stdout.write(f"Updating sending limit for {len(emails)} account(s) to {daily_limit}...")
        self.stdout.write(f"Headers: {json.dumps({k:v for k,v in headers.items() if k != 'Cookie'}, indent=2)}")
        self.stdout.write(f"Data: {json.dumps(data, indent=2)}")
        
        try:
            response = requests.post(url, headers=headers, json=data)
            response.raise_for_status()
            self.stdout.write(self.style.SUCCESS(f"✓ Sending limit updated successfully to {daily_limit}"))
        except requests.exceptions.RequestException as e:
            self.stdout.write(self.style.ERROR(f"✗ Failed to update sending limit: {str(e)}"))
            if hasattr(e.response, 'text'):
                self.stdout.write(self.style.ERROR(f"Response: {e.response.text}"))

    async def process_campaign(self, session, campaign, user_settings):
        """Process a single campaign and generate report"""
        try:
            self.stdout.write(f"\n=== Calling EmailGuard API ===")
            url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{campaign.emailguard_tag}"
            self.stdout.write(f"Endpoint: {url}")
            self.stdout.write(f"Method: GET")
            
            headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}"
            }
            self.stdout.write(f"Request Headers: {json.dumps(headers, indent=2)}")
            
            response = requests.get(url, headers=headers)
            self.stdout.write(f"Response Status: {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                self.stdout.write("  API call successful")
                self.stdout.write(f"  Raw EmailGuard Response: {json.dumps(data, indent=2)}")

            # Calculate scores
            google_score = self.calculate_score(data, 'Google')
            outlook_score = self.calculate_score(data, 'Microsoft')

            self.stdout.write(f"  Google Score: {google_score}")
            self.stdout.write(f"  Outlook Score: {outlook_score}")
            
            # Create or update report
            report, created = UserSpamcheckReport.objects.update_or_create(
                spamcheck_instantly=campaign.spamcheck,
                email_account=campaign.account_id.email_account,
                defaults={
                    'organization': campaign.organization,
                    'google_pro_score': google_score,
                    'outlook_pro_score': outlook_score,
                    'report_link': f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}"
                }
            )
            
            self.stdout.write(f"  Report {'created' if created else 'updated'} successfully")
            
            # Store domain scores if is_domain_based is True
            if campaign.spamcheck.is_domain_based and campaign.account_id and campaign.account_id.email_account:
                domain = campaign.account_id.email_account.split('@')[1]
                campaign.spamcheck.domain_scores[domain].append((
                    google_score,
                    outlook_score,
                    f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}"
                ))
                self.stdout.write(f"  Stored scores for domain {domain}")
            
            # Update sending limits based on conditions
            if campaign.spamcheck.conditions:
                self.stdout.write(f"  Checking conditions: {campaign.spamcheck.conditions}")
                self.evaluate_conditions(campaign.spamcheck, google_score, outlook_score, campaign.account_id.email_account)
            else:
                self.stdout.write("  No conditions specified, using default")
                if google_score >= 0.5:
                    self.update_sending_limit(campaign.organization, campaign.account_id.email_account, 25)
                    self.stdout.write("  Applied default condition: google>=0.5sending=25/1")

            # Delete campaign from Instantly after processing
            try:
                # Get API key from user_instantly
                user_instantly = UserInstantly.objects.get(
                    user=campaign.spamcheck.user,
                    instantly_organization_id=campaign.spamcheck.user_organization.instantly_organization_id
                )
                delete_url = f"https://api.instantly.ai/api/v2/campaigns/{campaign.instantly_campaign_id}"
                delete_headers = {
                    "Authorization": f"Bearer {user_instantly.instantly_api_key}"
                }
                self.stdout.write(f"  Deleting campaign {campaign.instantly_campaign_id} from Instantly...")
                delete_response = requests.delete(delete_url, headers=delete_headers)
                if delete_response.status_code == 200:
                    campaign.campaign_status = 'deleted'
                    campaign.save()
                    self.stdout.write(self.style.SUCCESS(f"  ✓ Campaign {campaign.instantly_campaign_id} deleted successfully"))
                else:
                    self.stdout.write(self.style.ERROR(f"  ✗ Failed to delete campaign: {delete_response.text}"))
            except UserInstantly.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"  ✗ No API key found for organization"))
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"  ✗ Error deleting campaign: {str(e)}"))
            
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

            # Store domain scores if is_domain_based is True
            domain_scores = defaultdict(list)  # {domain: [(google_score, outlook_score, report_link), ...]}

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

                # Process domain-based scores for unused accounts
                if spamcheck.is_domain_based and domain_scores:
                    self.stdout.write("\nProcessing domain-based scores for unused accounts...")
                    
                    # Get all accounts for this spamcheck
                    all_accounts = spamcheck.accounts.all()
                    processed_accounts = set(campaign.account_id.email_account for campaign in spamcheck.campaigns.all())
                    
                    # Group unused accounts by domain
                    unused_accounts_by_domain = defaultdict(list)
                    for account in all_accounts:
                        if account.email_account not in processed_accounts:
                            domain = account.email_account.split('@')[1]
                            if domain in domain_scores:
                                unused_accounts_by_domain[domain].append(account.email_account)
                    
                    # Process each domain's accounts in bulk
                    for domain, accounts in unused_accounts_by_domain.items():
                        self.stdout.write(f"\n  Processing {len(accounts)} unused accounts for domain {domain}")
                        
                        # Create reports for all accounts in this domain
                        google_score = domain_scores[domain][0][0]  # Use first score from domain
                        outlook_score = domain_scores[domain][0][1]
                        report_link = domain_scores[domain][0][2]
                        
                        # Bulk create reports
                        reports_to_create = []
                        for email in accounts:
                            reports_to_create.append(
                                UserSpamcheckReport(
                                    spamcheck_instantly=spamcheck,
                                    email_account=email,
                                    organization=spamcheck.user_organization,
                                    google_pro_score=google_score,
                                    outlook_pro_score=outlook_score,
                                    report_link=report_link
                                )
                            )
                        
                        # Bulk create reports
                        UserSpamcheckReport.objects.bulk_create(reports_to_create)
                        self.stdout.write(f"  Created {len(reports_to_create)} reports for domain {domain}")
                        
                        # If conditions were met for the original account, update all accounts in bulk
                        if self.conditions_met:  # Use the stored result from evaluate_conditions
                            daily_limit = self.parse_conditions(spamcheck.conditions)['daily_limit']
                            self.stdout.write(f"  Updating sending limits for all accounts to {daily_limit}...")
                            
                            # Update all accounts in one request
                            self.update_sending_limit(
                                spamcheck.user_organization,
                                accounts,  # Pass the full list of emails
                                daily_limit
                            )
                            
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
        self.stdout.write("\nAPI Endpoints being used:")
        self.stdout.write("1. EmailGuard Report: GET https://app.emailguard.io/api/v1/inbox-placement-tests/{tag}")
        self.stdout.write("2. Instantly Update Account: POST https://app.instantly.ai/backend/api/v1/account/update/bulk")
        self.stdout.write("3. Instantly Delete Campaign: DELETE https://api.instantly.ai/api/v2/campaigns/{id}")
        self.stdout.write("---\n")

        # Get spamchecks that need reports
        spamchecks = UserSpamcheck.objects.filter(
            status='generating_reports'
        ).prefetch_related('campaigns', 'accounts')

        self.stdout.write(f"\nFound {spamchecks.count()} spamchecks in generating_reports status")

        for spamcheck in spamchecks:
            self.stdout.write(f"\nProcessing Spamcheck ID: {spamcheck.id}")
            self.stdout.write(f"Last updated: {spamcheck.updated_at}")
            
            # Calculate waiting time
            waiting_time = spamcheck.reports_waiting_time if spamcheck.reports_waiting_time is not None else 1.0  # Default 1h only if not provided
            self.stdout.write(f"Waiting time configured: {waiting_time} hours")
            
            time_since_update = (timezone.now() - spamcheck.updated_at).total_seconds() / 3600
            self.stdout.write(f"Time since last update: {time_since_update:.2f} hours")

            if time_since_update < waiting_time:
                self.stdout.write(f"Not enough time has passed. Skipping...")
                continue

            self.stdout.write("Enough time has passed. Processing campaigns...")
            
            # Store domain scores if is_domain_based is True
            domain_scores = defaultdict(list)  # {domain: [(google_score, outlook_score, report_link), ...]}
            
            all_reports_generated = True  # Track if all reports were generated successfully
            
            # Process each campaign
            for campaign in spamcheck.campaigns.all():
                self.stdout.write(f"\n  Campaign ID: {campaign.id}")
                self.stdout.write(f"  EmailGuard Tag: {campaign.emailguard_tag}")
                
                try:
                    # Get user settings first
                    user_settings = UserSettings.objects.get(user=spamcheck.user)
                    
                    # Call EmailGuard API
                    url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{campaign.emailguard_tag}"
                    headers = {
                        "Authorization": f"Bearer {user_settings.emailguard_api_key}"
                    }
                    
                    self.stdout.write("  Calling EmailGuard API...")
                    response = requests.get(url, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        self.stdout.write("  API call successful")
                        self.stdout.write(f"  Raw EmailGuard Response: {json.dumps(data, indent=2)}")
                        
                        # Calculate scores
                        google_score = self.calculate_score(data, 'Google')
                        outlook_score = self.calculate_score(data, 'Microsoft')
                        
                        self.stdout.write(f"  Google Score: {google_score}")
                        self.stdout.write(f"  Outlook Score: {outlook_score}")
                        
                        # Create or update report
                        report, created = UserSpamcheckReport.objects.update_or_create(
                            spamcheck_instantly=spamcheck,
                            email_account=campaign.account_id.email_account,
                            defaults={
                                'organization': spamcheck.user_organization,
                                'google_pro_score': google_score,
                                'outlook_pro_score': outlook_score,
                                'report_link': f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}"
                            }
                        )
                        
                        self.stdout.write(f"  Report {'created' if created else 'updated'} successfully")
                        
                        # Store domain scores if is_domain_based is True
                        if spamcheck.is_domain_based and campaign.account_id and campaign.account_id.email_account:
                            domain = campaign.account_id.email_account.split('@')[1]
                            domain_scores[domain].append((
                                google_score,
                                outlook_score,
                                f"https://app.emailguard.io/inbox-placement-tests/{campaign.emailguard_tag}"
                            ))
                            self.stdout.write(f"  Stored scores for domain {domain}")
                        
                        # Update sending limits based on conditions
                        if spamcheck.conditions:
                            self.stdout.write(f"  Checking conditions: {spamcheck.conditions}")
                            self.evaluate_conditions(spamcheck, google_score, outlook_score, campaign.account_id.email_account)
                        else:
                            self.stdout.write("  No conditions specified, using default")
                            if google_score >= 0.5:
                                self.update_sending_limit(spamcheck.user_organization, campaign.account_id.email_account, 25)
                                self.stdout.write("  Applied default condition: google>=0.5sending=25/1")

                        # Delete campaign from Instantly after processing
                        try:
                            # Get API key from user_instantly
                            user_instantly = UserInstantly.objects.get(
                                user=spamcheck.user,
                                instantly_organization_id=spamcheck.user_organization.instantly_organization_id
                            )
                            delete_url = f"https://api.instantly.ai/api/v2/campaigns/{campaign.instantly_campaign_id}"
                            delete_headers = {
                                "Authorization": f"Bearer {user_instantly.instantly_api_key}"
                            }
                            self.stdout.write(f"  Deleting campaign {campaign.instantly_campaign_id} from Instantly...")
                            delete_response = requests.delete(delete_url, headers=delete_headers)
                            if delete_response.status_code == 200:
                                campaign.campaign_status = 'deleted'
                                campaign.save()
                                self.stdout.write(self.style.SUCCESS(f"  ✓ Campaign {campaign.instantly_campaign_id} deleted successfully"))
                            else:
                                self.stdout.write(self.style.ERROR(f"  ✗ Failed to delete campaign: {delete_response.text}"))
                        except UserInstantly.DoesNotExist:
                            self.stdout.write(self.style.ERROR(f"  ✗ No API key found for organization"))
                        except Exception as e:
                            self.stdout.write(self.style.ERROR(f"  ✗ Error deleting campaign: {str(e)}"))
                            
                    else:
                        all_reports_generated = False
                        self.stdout.write(self.style.ERROR(f"  API call failed with status {response.status_code}"))
                        continue
                        
                except Exception as e:
                    all_reports_generated = False
                    self.stdout.write(self.style.ERROR(f"  Error processing campaign: {str(e)}"))
                    continue

            # Process domain-based scores for unused accounts
            if spamcheck.is_domain_based and domain_scores:
                try:
                    self.stdout.write("\nProcessing domain-based scores for unused accounts...")
                    
                    # Get all accounts for this spamcheck
                    all_accounts = spamcheck.accounts.all()
                    processed_accounts = set(campaign.account_id.email_account for campaign in spamcheck.campaigns.all())
                    
                    # Group unused accounts by domain
                    unused_accounts_by_domain = defaultdict(list)
                    for account in all_accounts:
                        if account.email_account not in processed_accounts:
                            domain = account.email_account.split('@')[1]
                            if domain in domain_scores:
                                unused_accounts_by_domain[domain].append(account.email_account)
                    
                    # Process each domain's accounts in bulk
                    for domain, accounts in unused_accounts_by_domain.items():
                        self.stdout.write(f"\n  Processing {len(accounts)} unused accounts for domain {domain}")
                        
                        # Create reports for all accounts in this domain
                        google_score = domain_scores[domain][0][0]  # Use first score from domain
                        outlook_score = domain_scores[domain][0][1]
                        report_link = domain_scores[domain][0][2]
                        
                        # Bulk create reports
                        reports_to_create = []
                        for email in accounts:
                            reports_to_create.append(
                                UserSpamcheckReport(
                                    spamcheck_instantly=spamcheck,
                                    email_account=email,
                                    organization=spamcheck.user_organization,
                                    google_pro_score=google_score,
                                    outlook_pro_score=outlook_score,
                                    report_link=report_link
                                )
                            )
                        
                        # Bulk create reports
                        UserSpamcheckReport.objects.bulk_create(reports_to_create)
                        self.stdout.write(f"  Created {len(reports_to_create)} reports for domain {domain}")
                        
                        # If conditions were met for the original account, update all accounts in bulk
                        if self.conditions_met:  # Use the stored result from evaluate_conditions
                            daily_limit = self.parse_conditions(spamcheck.conditions)['daily_limit']
                            self.stdout.write(f"  Updating sending limits for all accounts to {daily_limit}...")
                            
                            # Update all accounts in one request
                            self.update_sending_limit(
                                spamcheck.user_organization,
                                accounts,  # Pass the full list of emails
                                daily_limit
                            )
                            
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f"  Error processing unused accounts: {str(e)}"))

            # Only update status if all reports were generated successfully
            if all_reports_generated:
                spamcheck.status = 'completed'
                spamcheck.save()
                self.stdout.write(self.style.SUCCESS(f"All reports generated successfully. Spamcheck {spamcheck.id} marked as completed"))
            else:
                self.stdout.write(self.style.WARNING(f"Some reports failed to generate for spamcheck {spamcheck.id}. Status remains as generating_reports"))

        self.stdout.write("\n=== Report Generation Complete ===") 