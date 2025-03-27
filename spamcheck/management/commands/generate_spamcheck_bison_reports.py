"""
This command generates comprehensive reports for completed Bison spamcheck campaigns.

Purpose:
- Finds spamchecks in 'generating_reports' status
- Respects waiting time before generating reports (based on spamcheck's updated_at time)
- Gets report data from EmailGuard API
- Creates/updates reports in database with:
  * Google and Outlook scores
  * Account status (good/bad)
  * Used email subject and body
  * Account tags
  * Sending limits based on conditions
- Updates account sending limits in Bison
- Handles domain-based spamchecks:
  * When enabled, applies same scores and limits to all accounts with same domain
  * Only processes accounts that are part of the same spamcheck
  * Maintains consistency across domain-based reporting

Flow:
1. Finds spamchecks ready for report generation:
   - Status must be 'generating_reports'
   - Must satisfy waiting time condition

2. For each spamcheck:
   - Gets EmailGuard report data
   - Calculates scores
   - Creates comprehensive reports
   - Updates sending limits in Bison API
   - If domain-based is enabled:
     * Finds all accounts with same domain
     * Applies same scores and limits
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.db.models import Q, F
from spamcheck.models import (
    UserSpamcheckBison,
    UserSpamcheckAccountsBison,
    UserSpamcheckBisonReport,
    SpamcheckErrorLog
)
from settings.models import UserSettings
from asgiref.sync import sync_to_async
import aiohttp
import asyncio
from datetime import timedelta
import json
from collections import defaultdict
import requests
import logging
import traceback
from settings.utils import send_webhook

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Generate reports for completed Bison spamcheck campaigns'

    def __init__(self):
        super().__init__()
        self.rate_limit = asyncio.Semaphore(10)
        self.conditions_met = False
        self.processed_accounts = set()
        self.skipped_accounts = []
        self.failed_accounts = []

    def calculate_score(self, data, provider):
        """Calculate provider score from EmailGuard data"""
        if not data or 'data' not in data or 'inbox_placement_test_emails' not in data['data']:
            return 0.0

        emails = data['data']['inbox_placement_test_emails']
        provider_emails = [e for e in emails if e['provider'] == provider]
        
        if not provider_emails:
            return 0.0

        total_emails = len(provider_emails)
        inbox_count = sum(1 for e in provider_emails if e.get('folder') and e.get('folder', '').lower() == 'inbox')
        score = inbox_count / total_emails if total_emails > 0 else 0.0
        return round(score, 2)

    def parse_conditions(self, conditions_str):
        """Parse conditions string into dict"""
        if not conditions_str:
            return {
                'google': 0.5, 
                'outlook': 0.5, 
                'good_limit': 25, 
                'bad_limit': 3,
                'google_operator': '>=',
                'outlook_operator': '>=',
                'logic_operator': 'and'  # Default to AND logic
            }

        conditions = {}
        
        # Identify the logic operator (and/or)
        conditions_str = conditions_str.lower()
        if 'and' in conditions_str:
            conditions['logic_operator'] = 'and'
        elif 'or' in conditions_str:
            conditions['logic_operator'] = 'or'
        else:
            conditions['logic_operator'] = 'and'  # Default to AND logic
        
        # Split by the detected logic operator to get parts
        score_parts = []
        if 'and' in conditions_str:
            parts = conditions_str.split('sending=')
            if len(parts) >= 1:
                score_parts = parts[0].split('and')
        elif 'or' in conditions_str:
            parts = conditions_str.split('sending=')
            if len(parts) >= 1:
                score_parts = parts[0].split('or')
        else:
            # Handle malformed conditions by falling back to defaults
            self.stdout.write(self.style.WARNING(f"⚠️ Malformed conditions string: {conditions_str}. Using defaults."))
            return {
                'google': 0.5, 
                'outlook': 0.5, 
                'good_limit': 25, 
                'bad_limit': 3,
                'google_operator': '>=',
                'outlook_operator': '>=',
                'logic_operator': 'and'
            }
        
        # Get the sending limits part
        limits_part = "25"  # Default
        if 'sending=' in conditions_str:
            limits_part = conditions_str.split('sending=')[1].strip()
            
        # Parse scores
        for part in score_parts:
            part = part.strip()
            if 'google' in part:
                if '>=' in part:
                    try:
                        conditions['google'] = float(part.split('>=')[1])
                        conditions['google_operator'] = '>='
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Google condition format: {part}. Using default 0.5"))
                        conditions['google'] = 0.5
                        conditions['google_operator'] = '>='
                elif '<=' in part:
                    try:
                        conditions['google'] = float(part.split('<=')[1])
                        conditions['google_operator'] = '<='
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Google condition format: {part}. Using default 0.5"))
                        conditions['google'] = 0.5
                        conditions['google_operator'] = '<='
                elif '>' in part:
                    try:
                        conditions['google'] = float(part.split('>')[1])
                        conditions['google_operator'] = '>'
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Google condition format: {part}. Using default 0.5"))
                        conditions['google'] = 0.5
                        conditions['google_operator'] = '>'
                elif '<' in part:
                    try:
                        conditions['google'] = float(part.split('<')[1])
                        conditions['google_operator'] = '<'
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Google condition format: {part}. Using default 0.5"))
                        conditions['google'] = 0.5
                        conditions['google_operator'] = '<'
            elif 'outlook' in part:
                if '>=' in part:
                    try:
                        conditions['outlook'] = float(part.split('>=')[1])
                        conditions['outlook_operator'] = '>='
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Outlook condition format: {part}. Using default 0.5"))
                        conditions['outlook'] = 0.5
                        conditions['outlook_operator'] = '>='
                elif '<=' in part:
                    try:
                        conditions['outlook'] = float(part.split('<=')[1])
                        conditions['outlook_operator'] = '<='
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Outlook condition format: {part}. Using default 0.5"))
                        conditions['outlook'] = 0.5
                        conditions['outlook_operator'] = '<='
                elif '>' in part:
                    try:
                        conditions['outlook'] = float(part.split('>')[1])
                        conditions['outlook_operator'] = '>'
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Outlook condition format: {part}. Using default 0.5"))
                        conditions['outlook'] = 0.5
                        conditions['outlook_operator'] = '>'
                elif '<' in part:
                    try:
                        conditions['outlook'] = float(part.split('<')[1])
                        conditions['outlook_operator'] = '<'
                    except ValueError:
                        self.stdout.write(self.style.WARNING(f"⚠️ Invalid Outlook condition format: {part}. Using default 0.5"))
                        conditions['outlook'] = 0.5
                        conditions['outlook_operator'] = '<'
        
        # Parse sending limits
        if '/' in limits_part:
            try:
                good_limit, bad_limit = limits_part.split('/')
                conditions['good_limit'] = int(good_limit)
                conditions['bad_limit'] = int(bad_limit)
            except (ValueError, IndexError):
                self.stdout.write(self.style.WARNING(f"⚠️ Invalid sending limits format: {limits_part}. Using defaults 25/3"))
                conditions['good_limit'] = 25
                conditions['bad_limit'] = 3
        else:
            try:
                conditions['good_limit'] = int(limits_part)
                conditions['bad_limit'] = 3
            except ValueError:
                self.stdout.write(self.style.WARNING(f"⚠️ Invalid good limit format: {limits_part}. Using default 25"))
                conditions['good_limit'] = 25
                conditions['bad_limit'] = 3
        
        # Set defaults for missing values
        if 'google' not in conditions:
            conditions['google'] = 0.5
            conditions['google_operator'] = '>='
        if 'outlook' not in conditions:
            conditions['outlook'] = 0.5
            conditions['outlook_operator'] = '>='
        if 'good_limit' not in conditions:
            conditions['good_limit'] = 25
        if 'bad_limit' not in conditions:
            conditions['bad_limit'] = 3

        # Log the parsed conditions
        self.stdout.write(f"📊 Parsed conditions: Google {conditions['google_operator']} {conditions['google']} {conditions['logic_operator'].upper()} Outlook {conditions['outlook_operator']} {conditions['outlook']}, Good limit: {conditions['good_limit']}, Bad limit: {conditions['bad_limit']}")

        return conditions

    def evaluate_conditions(self, spamcheck, google_score, outlook_score):
        """Evaluate if scores meet conditions and return appropriate sending limit"""
        conditions = self.parse_conditions(spamcheck.conditions)
        
        def evaluate_condition(score, target, operator):
            if operator == '>':
                return score > target
            elif operator == '>=':
                return score >= target
            elif operator == '<':
                return score < target
            elif operator == '<=':
                return score <= target
            return False
        
        google_ok = evaluate_condition(
            google_score,
            conditions['google'],
            conditions['google_operator']
        )
        outlook_ok = evaluate_condition(
            outlook_score,
            conditions['outlook'],
            conditions['outlook_operator']
        )
        
        # Apply AND/OR logic based on the detected logic operator
        if conditions.get('logic_operator') == 'or':
            self.conditions_met = google_ok or outlook_ok
        else:  # Default to 'and'
            self.conditions_met = google_ok and outlook_ok
        
        sending_limit = conditions.get('good_limit', 25) if self.conditions_met else conditions.get('bad_limit', 3)
        
        # Log the evaluation results
        self.stdout.write(f"   📊 Condition evaluation: Google score {google_score} {conditions['google_operator']} {conditions['google']} = {google_ok}")
        self.stdout.write(f"   📊 Condition evaluation: Outlook score {outlook_score} {conditions['outlook_operator']} {conditions['outlook']} = {outlook_ok}")
        self.stdout.write(f"   📊 Logic operator: {conditions['logic_operator'].upper()}")
        self.stdout.write(f"   📊 Conditions met: {self.conditions_met}, Sending limit: {sending_limit}")
        
        return self.conditions_met, sending_limit

    async def update_sending_limit(self, organization, email_id, daily_limit):
        """Update sending limit for email account in Bison"""
        try:
            url = f"{organization.base_url.rstrip('/')}/api/sender-emails/{email_id}"
            headers = {
                "Authorization": f"Bearer {organization.bison_organization_api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "daily_limit": daily_limit
            }
            
            response = await asyncio.to_thread(
                requests.patch,
                url,
                headers=headers,
                json=data
            )
            
            if response.status_code != 200:
                self.failed_accounts.append(f"{email_id} (Failed to update sending limit)")
                return False
                
            return True
                
        except Exception as e:
            self.failed_accounts.append(f"{email_id} (Error updating sending limit)")
            return False

    async def get_account_tags(self, organization, email):
        """Get tags for a specific email account from Bison API"""
        try:
            url = f"{organization.base_url.rstrip('/')}/api/sender-emails/{email}"
            headers = {
                "Authorization": f"Bearer {organization.bison_organization_api_key}"
            }
            
            response = await asyncio.to_thread(
                requests.get,
                url,
                headers=headers
            )
            
            if response.status_code == 200:
                data = response.json()
                if 'data' in data:
                    # Get tags
                    tags_str = None
                    if 'tags' in data['data']:
                        tag_names = [tag['name'] for tag in data['data']['tags']]
                        tags_str = ','.join(tag_names)
                    
                    # Get bounced, unique reply, and emails sent counts
                    bounced_count = data['data'].get('bounced_count', 0)
                    unique_replied_count = data['data'].get('unique_replied_count', 0)
                    emails_sent_count = data['data'].get('emails_sent_count', 0)
                    
                    return tags_str, bounced_count, unique_replied_count, emails_sent_count
                return None, 0, 0, 0
                
        except Exception:
            return None, 0, 0, 0

    @sync_to_async
    def get_user_settings(self, user):
        """Get user settings synchronously"""
        return UserSettings.objects.get(user=user)

    @sync_to_async
    def get_accounts(self, spamcheck):
        """Get accounts with prefetched related data"""
        return list(
            UserSpamcheckAccountsBison.objects.filter(bison_spamcheck=spamcheck)
            .select_related('organization', 'organization__user', 'bison_spamcheck')
        )

    def get_domain_from_email(self, email):
        """Extract domain from email address"""
        try:
            return email.split('@')[1]
        except:
            return None

    @sync_to_async
    def get_all_spamcheck_accounts(self, spamcheck):
        """Get all accounts associated with a spamcheck"""
        return list(UserSpamcheckAccountsBison.objects.filter(
            bison_spamcheck=spamcheck
        ).select_related('organization'))

    async def get_domain_accounts(self, spamcheck, domain):
        """Get all accounts from a spamcheck that share the same domain"""
        accounts = await self.get_all_spamcheck_accounts(spamcheck)
        return [
            account for account in accounts
            if self.get_domain_from_email(account.email_account) == domain
        ]

    async def process_account(self, account, user_settings):
        """Process a single account and generate report"""
        try:
            self.stdout.write(f"   Processing account: {account.email_account}")
            
            if not account.last_emailguard_tag:
                self.stdout.write(f"   ⚠️ No EmailGuard tag for account {account.email_account}")
                self.skipped_accounts.append(f"{account.email_account} (No EmailGuard tag)")
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=account.bison_spamcheck.user,
                    bison_spamcheck=account.bison_spamcheck,
                    error_type='validation_error',
                    provider='emailguard',
                    error_message=f"No EmailGuard tag for account {account.email_account}",
                    account_email=account.email_account,
                    step='report_generation'
                )
                return False
            
            # Get EmailGuard report
            self.stdout.write(f"   📡 Fetching EmailGuard report for {account.email_account}")
            url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{account.last_emailguard_tag}"
            headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}"
            }
            
            try:
                response = await asyncio.to_thread(requests.get, url, headers=headers)
                
                if response.status_code == 200:
                    self.stdout.write(f"   ✅ Got EmailGuard report for {account.email_account}")
                    data = response.json()

                    # Calculate scores
                    google_score = self.calculate_score(data, 'Google')
                    outlook_score = self.calculate_score(data, 'Microsoft')
                    self.stdout.write(f"   📊 Scores - Google: {google_score}, Outlook: {outlook_score}")

                    # Evaluate conditions and get appropriate sending limit
                    is_good, sending_limit = self.evaluate_conditions(
                        account.bison_spamcheck, 
                        google_score, 
                        outlook_score
                    )
                    self.stdout.write(f"   📊 Good: {is_good}, Sending limit: {sending_limit}")

                    # Get account tags from Bison API
                    self.stdout.write(f"   🏷️ Getting tags for {account.email_account}")
                    tags_str, bounced_count, unique_replied_count, emails_sent_count = await self.get_account_tags(account.organization, account.email_account)

                    # Process domain-based accounts if enabled
                    if account.bison_spamcheck.is_domain_based:
                        self.stdout.write(f"   🔄 Processing domain-based account {account.email_account}")
                        campaign_domain = self.get_domain_from_email(account.email_account)
                        domain_accounts = await self.get_domain_accounts(account.bison_spamcheck, campaign_domain)
                        
                        # Create reports for all accounts in this domain
                        processed_emails = set()
                        for domain_account in domain_accounts:
                            if domain_account.email_account in processed_emails:
                                continue
                            processed_emails.add(domain_account.email_account)
                            
                            domain_tags_str, domain_bounced_count, domain_unique_replied_count, domain_emails_sent_count = await self.get_account_tags(domain_account.organization, domain_account.email_account)
                            
                            # Create report (always create a new one instead of updating)
                            self.stdout.write(f"   📝 Creating report for domain account {domain_account.email_account}")
                            report = await asyncio.to_thread(
                                lambda: UserSpamcheckBisonReport.objects.create(
                                    spamcheck_bison=account.bison_spamcheck,
                                    email_account=domain_account.email_account,
                                    bison_organization=domain_account.organization,
                                    google_pro_score=google_score,
                                    outlook_pro_score=outlook_score,
                                    report_link=f"https://app.emailguard.io/inbox-placement-tests/{account.last_emailguard_tag}",
                                    is_good=is_good,
                                    used_subject=account.bison_spamcheck.subject,
                                    used_body=account.bison_spamcheck.body,
                                    sending_limit=sending_limit,
                                    tags_list=domain_tags_str,
                                    workspace_name=domain_account.organization.bison_organization_name,
                                    bounced_count=domain_bounced_count,
                                    unique_replied_count=domain_unique_replied_count,
                                    emails_sent_count=domain_emails_sent_count
                                )
                            )
                            
                            # Update sending limit if enabled
                            if account.bison_spamcheck.update_sending_limit:
                                self.stdout.write(f"   📤 Updating sending limit for {domain_account.email_account}")
                                success = await self.update_sending_limit(domain_account.organization, domain_account.email_account, sending_limit)
                                if success:
                                    self.processed_accounts.add(domain_account.email_account)
                                    self.stdout.write(f"   ✅ Successfully processed {domain_account.email_account}")
                                else:
                                    self.stdout.write(f"   ❌ Failed to update sending limit for {domain_account.email_account}")
                                    # Log the error
                                    await asyncio.to_thread(
                                        SpamcheckErrorLog.objects.create,
                                        user=account.bison_spamcheck.user,
                                        bison_spamcheck=account.bison_spamcheck,
                                        error_type='api_error',
                                        provider='bison',
                                        error_message=f"Failed to update sending limit for {domain_account.email_account}",
                                        account_email=domain_account.email_account,
                                        step='update_sending_limit'
                                    )
                            else:
                                self.stdout.write(f"   ℹ️ Skipping sending limit update for {domain_account.email_account} (update_sending_limit is disabled)")
                                self.processed_accounts.add(domain_account.email_account)
                                self.stdout.write(f"   ✅ Successfully processed {domain_account.email_account}")
                    else:
                        # Single account processing
                        self.stdout.write(f"   📝 Creating report for {account.email_account}")
                        tags_str, bounced_count, unique_replied_count, emails_sent_count = await self.get_account_tags(account.organization, account.email_account)
                        
                        report = await asyncio.to_thread(
                            lambda: UserSpamcheckBisonReport.objects.create(
                                spamcheck_bison=account.bison_spamcheck,
                                email_account=account.email_account,
                                bison_organization=account.organization,
                                google_pro_score=google_score,
                                outlook_pro_score=outlook_score,
                                report_link=f"https://app.emailguard.io/inbox-placement-tests/{account.last_emailguard_tag}",
                                is_good=is_good,
                                used_subject=account.bison_spamcheck.subject,
                                used_body=account.bison_spamcheck.body,
                                sending_limit=sending_limit,
                                tags_list=tags_str,
                                workspace_name=account.organization.bison_organization_name,
                                bounced_count=bounced_count,
                                unique_replied_count=unique_replied_count,
                                emails_sent_count=emails_sent_count
                            )
                        )
                        
                        # Update sending limit if enabled
                        if account.bison_spamcheck.update_sending_limit:
                            self.stdout.write(f"   📤 Updating sending limit for {account.email_account}")
                            success = await self.update_sending_limit(account.organization, account.email_account, sending_limit)
                            if success:
                                self.processed_accounts.add(account.email_account)
                                self.stdout.write(f"   ✅ Successfully processed {account.email_account}")
                            else:
                                self.stdout.write(f"   ❌ Failed to update sending limit for {account.email_account}")
                                # Log the error
                                await asyncio.to_thread(
                                    SpamcheckErrorLog.objects.create,
                                    user=account.bison_spamcheck.user,
                                    bison_spamcheck=account.bison_spamcheck,
                                    error_type='api_error',
                                    provider='bison',
                                    error_message=f"Failed to update sending limit for {account.email_account}",
                                    account_email=account.email_account,
                                    step='update_sending_limit'
                                )
                        else:
                            self.stdout.write(f"   ℹ️ Skipping sending limit update for {account.email_account} (update_sending_limit is disabled)")
                            self.processed_accounts.add(account.email_account)
                            self.stdout.write(f"   ✅ Successfully processed {account.email_account}")

                    return True
                else:
                    self.stdout.write(self.style.ERROR(f"   ❌ EmailGuard API failed for {account.email_account}: {response.status_code}"))
                    self.failed_accounts.append(f"{account.email_account} (EmailGuard API failed: {response.status_code})")
                    
                    # Log the error
                    await asyncio.to_thread(
                        SpamcheckErrorLog.objects.create,
                        user=account.bison_spamcheck.user,
                        bison_spamcheck=account.bison_spamcheck,
                        error_type='api_error',
                        provider='emailguard',
                        error_message=f"EmailGuard API failed with status code {response.status_code}",
                        account_email=account.email_account,
                        step='fetch_emailguard_report',
                        api_endpoint=url,
                        status_code=response.status_code
                    )
                    return False
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"   ❌ Error fetching EmailGuard report for {account.email_account}: {str(e)}"))
                self.failed_accounts.append(f"{account.email_account} (Error fetching report: {str(e)})")
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=account.bison_spamcheck.user,
                    bison_spamcheck=account.bison_spamcheck,
                    error_type='connection_error' if 'connection' in str(e).lower() else 'unknown_error',
                    provider='emailguard',
                    error_message=str(e),
                    error_details={'full_error': str(e)},
                    account_email=account.email_account,
                    step='fetch_emailguard_report',
                    api_endpoint=url
                )
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"   ❌ Error processing account {account.email_account}: {str(e)}"))
            self.failed_accounts.append(f"{account.email_account} (Processing error: {str(e)})")
            
            # Log the error
            try:
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=account.bison_spamcheck.user,
                    bison_spamcheck=account.bison_spamcheck,
                    error_type='unknown_error',
                    provider='system',
                    error_message=str(e),
                    error_details={'full_error': str(e)},
                    account_email=account.email_account,
                    step='process_account'
                )
            except Exception as log_error:
                self.stdout.write(self.style.ERROR(f"   ❌ Error logging error: {str(log_error)}"))
            
            return False

    async def process_spamcheck(self, spamcheck):
        """Process a single spamcheck"""
        start_time = timezone.now()
        self.stdout.write(f"🚀 Starting to process spamcheck {spamcheck.id} ({spamcheck.name}) at {start_time}")
        
        try:
            # Reset tracking variables for this spamcheck
            self.processed_accounts = set()
            self.skipped_accounts = []
            self.failed_accounts = []
            
            try:
                self.stdout.write(f"🔍 Getting user settings for spamcheck {spamcheck.id}")
                user_settings = await self.get_user_settings(spamcheck.user)
            except UserSettings.DoesNotExist:
                error_message = f"User settings not found for spamcheck {spamcheck.id}"
                self.stdout.write(self.style.ERROR(f"❌ {error_message}"))
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='validation_error',
                    provider='system',
                    error_message=error_message,
                    step='get_user_settings'
                )
                
                # Set spamcheck status to failed
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                
                return False

            # Get accounts with related data prefetched
            self.stdout.write(f"📂 Fetching accounts for spamcheck {spamcheck.id}")
            accounts = await self.get_accounts(spamcheck)
            total_accounts = len(accounts)

            if not accounts:
                error_message = f"No accounts found for spamcheck {spamcheck.id}"
                self.stdout.write(self.style.WARNING(f"⚠️ {error_message}"))
                
                # Log the error
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='validation_error',
                    provider='system',
                    error_message=error_message,
                    step='get_accounts'
                )
                
                # Set spamcheck status to failed
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                
                return False

            self.stdout.write(f"\n📊 Processing spamcheck {spamcheck.id}:")
            self.stdout.write(f"   Total accounts found: {total_accounts}")

            # Status is already changed to generating_reports at the beginning of handle_async
            # No need to change it again here

            # For domain-based spamchecks, group accounts by domain
            if spamcheck.is_domain_based:
                self.stdout.write(f"🔄 Processing domain-based spamcheck {spamcheck.id}")
                # Group accounts by domain
                domain_groups = {}
                domain_scores = {}  # Store scores per domain
                
                for account in accounts:
                    domain = self.get_domain_from_email(account.email_account)
                    if domain not in domain_groups:
                        domain_groups[domain] = []
                    domain_groups[domain].append(account)
                
                self.stdout.write(f"   Found {len(domain_groups)} unique domains")

                # First, process one account per domain to get scores
                domain_count = 0
                for domain, domain_accounts in domain_groups.items():
                    domain_count += 1
                    self.stdout.write(f"   Processing domain {domain_count}/{len(domain_groups)}: {domain} ({len(domain_accounts)} accounts)")
                    representative_account = domain_accounts[0]
                    
                    # Get EmailGuard report for the representative account
                    if not representative_account.last_emailguard_tag:
                        self.stdout.write(f"   ⚠️ No EmailGuard tag for representative account {representative_account.email_account}")
                        self.skipped_accounts.extend([f"{acc.email_account} (No EmailGuard tag)" for acc in domain_accounts])
                        continue

                    try:
                        self.stdout.write(f"   📡 Fetching EmailGuard report for {representative_account.email_account}")
                        url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{representative_account.last_emailguard_tag}"
                        headers = {
                            "Authorization": f"Bearer {user_settings.emailguard_api_key}"
                        }
                        
                        response = await asyncio.to_thread(requests.get, url, headers=headers)
                        
                        if response.status_code == 200:
                            self.stdout.write(f"   ✅ Got EmailGuard report for {representative_account.email_account}")
                            data = response.json()
                            google_score = self.calculate_score(data, 'Google')
                            outlook_score = self.calculate_score(data, 'Microsoft')
                            is_good, sending_limit = self.evaluate_conditions(spamcheck, google_score, outlook_score)
                            
                            self.stdout.write(f"   📊 Scores - Google: {google_score}, Outlook: {outlook_score}, Good: {is_good}, Limit: {sending_limit}")
                            
                            # Store scores for this domain
                            domain_scores[domain] = {
                                'google_score': google_score,
                                'outlook_score': outlook_score,
                                'is_good': is_good,
                                'sending_limit': sending_limit,
                                'report_link': f"https://app.emailguard.io/inbox-placement-tests/{representative_account.last_emailguard_tag}"
                            }
                        else:
                            self.stdout.write(self.style.ERROR(f"   ❌ Failed to get EmailGuard report: {response.status_code}"))
                            self.failed_accounts.extend([f"{acc.email_account} (EmailGuard API failed)" for acc in domain_accounts])
                            continue
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"   ❌ Error processing domain {domain}: {str(e)}"))
                        self.failed_accounts.extend([f"{acc.email_account} (Error: {str(e)})" for acc in domain_accounts])
                        continue

                # Now create reports for ALL accounts using their domain's scores
                self.stdout.write(f"   🔄 Creating reports for all accounts")
                account_count = 0
                for account in accounts:
                    account_count += 1
                    if account_count % 10 == 0:
                        self.stdout.write(f"   Progress: {account_count}/{total_accounts} accounts")
                        
                    domain = self.get_domain_from_email(account.email_account)
                    if domain not in domain_scores:
                        continue

                    try:
                        scores = domain_scores[domain]
                        tags_str, bounced_count, unique_replied_count, emails_sent_count = await self.get_account_tags(account.organization, account.email_account)

                        # Create/update report for each account
                        report = await asyncio.to_thread(
                            lambda: UserSpamcheckBisonReport.objects.create(
                                spamcheck_bison=spamcheck,
                                email_account=account.email_account,
                                bison_organization=account.organization,
                                google_pro_score=scores['google_score'],
                                outlook_pro_score=scores['outlook_score'],
                                report_link=scores['report_link'],
                                is_good=scores['is_good'],
                                used_subject=spamcheck.subject,
                                used_body=spamcheck.body,
                                sending_limit=scores['sending_limit'],
                                tags_list=tags_str,
                                workspace_name=account.organization.bison_organization_name,
                                bounced_count=bounced_count,
                                unique_replied_count=unique_replied_count,
                                emails_sent_count=emails_sent_count
                            )
                        )

                        # Update sending limit if enabled
                        if spamcheck.update_sending_limit:
                            success = await self.update_sending_limit(account.organization, account.email_account, scores['sending_limit'])
                            if success:
                                self.processed_accounts.add(account.email_account)
                        else:
                            self.stdout.write(f"   ℹ️ Skipping sending limit update for {account.email_account} (update_sending_limit is disabled)")
                            self.processed_accounts.add(account.email_account)
                    except Exception as e:
                        self.stdout.write(self.style.ERROR(f"   ❌ Error creating report for {account.email_account}: {str(e)}"))
                        self.failed_accounts.append(f"{account.email_account} (Error: {str(e)})")
            else:
                # Process all accounts in parallel for non-domain-based spamchecks
                self.stdout.write(f"🔄 Processing non-domain-based spamcheck {spamcheck.id}")
                
                # Process accounts in smaller batches to avoid overwhelming the system
                batch_size = 20
                for i in range(0, total_accounts, batch_size):
                    batch = accounts[i:i+batch_size]
                    self.stdout.write(f"   Processing batch {i//batch_size + 1}/{(total_accounts + batch_size - 1)//batch_size}: accounts {i+1}-{min(i+batch_size, total_accounts)}")
                    
                    results = await asyncio.gather(*[
                        self.process_account(account, user_settings)
                        for account in batch
                    ])
                    
                    self.stdout.write(f"   Batch {i//batch_size + 1} complete: {sum(1 for r in results if r)}/{len(batch)} successful")

            # Print processing summary
            processed_count = len(self.processed_accounts)
            skipped_count = len(self.skipped_accounts)
            failed_count = len(self.failed_accounts)
            end_time = timezone.now()
            processing_time = (end_time - start_time).total_seconds()

            self.stdout.write(f"\n📈 Processing Summary for spamcheck {spamcheck.id}:")
            self.stdout.write(f"   Started at: {start_time}")
            self.stdout.write(f"   Completed at: {end_time}")
            self.stdout.write(f"   Total processing time: {processing_time:.2f} seconds")
            self.stdout.write(f"   ✓ Successfully processed: {processed_count}")
            self.stdout.write(f"   ⚠️ Skipped: {skipped_count}")
            self.stdout.write(f"   ❌ Failed: {failed_count}")

            if skipped_count > 0:
                self.stdout.write("\n⚠️ Skipped accounts:")
                for account in self.skipped_accounts[:10]:  # Show only first 10
                    self.stdout.write(f"   - {account}")
                if skipped_count > 10:
                    self.stdout.write(f"   - ... and {skipped_count - 10} more")

            if failed_count > 0:
                self.stdout.write("\n❌ Failed accounts:")
                for account in self.failed_accounts[:10]:  # Show only first 10
                    self.stdout.write(f"   - {account}")
                if failed_count > 10:
                    self.stdout.write(f"   - ... and {failed_count - 10} more")

            # Only mark spamcheck as completed if we processed all accounts
            if processed_count > 0:
                self.stdout.write(f"✅ Marking spamcheck {spamcheck.id} as completed")
                spamcheck.status = 'completed'
                await asyncio.to_thread(spamcheck.save)
                
                # Get all reports for this spamcheck
                reports = await asyncio.to_thread(
                    lambda: list(UserSpamcheckBisonReport.objects.filter(
                        spamcheck_bison=spamcheck
                    ).values(
                        'id', 'email_account', 'google_pro_score', 'outlook_pro_score',
                        'report_link', 'is_good', 'sending_limit', 'tags_list',
                        'workspace_name', 'bounced_count', 'unique_replied_count',
                        'emails_sent_count', 'created_at', 'updated_at'
                    ))
                )
                
                # Send webhook if URL is configured
                await send_webhook(user_settings, spamcheck, reports)
                
                # Log success
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='info',
                    provider='system',
                    error_message=f"Spamcheck completed successfully. Processed: {processed_count}, Skipped: {skipped_count}, Failed: {failed_count}",
                    step='process_spamcheck_complete',
                    error_details={
                        'processing_time_seconds': processing_time,
                        'processed_count': processed_count,
                        'skipped_count': skipped_count,
                        'failed_count': failed_count
                    }
                )
                
                return True
            else:
                self.stdout.write(f"⚠️ No accounts processed for spamcheck {spamcheck.id}")
                
                # Set spamcheck status to failed if nothing processed
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                
                # Log failure
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='validation_error',
                    provider='system',
                    error_message=f"Spamcheck failed - no accounts were processed successfully",
                    step='process_spamcheck_complete',
                    error_details={
                        'processing_time_seconds': processing_time,
                        'skipped_count': skipped_count,
                        'failed_count': failed_count,
                        'skipped_accounts': self.skipped_accounts[:20],
                        'failed_accounts': self.failed_accounts[:20]
                    }
                )
                
                return False

        except Exception as e:
            end_time = timezone.now()
            processing_time = (end_time - start_time).total_seconds()
            error_message = f"Error processing spamcheck {spamcheck.id}: {str(e)}"
            self.stdout.write(self.style.ERROR(f"❌ {error_message}"))
            
            # Get traceback
            tb = traceback.format_exc()
            self.stdout.write(self.style.ERROR(tb))
            
            # Log the error
            try:
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    user=spamcheck.user,
                    bison_spamcheck=spamcheck,
                    error_type='unknown_error',
                    provider='system',
                    error_message=error_message,
                    error_details={
                        'full_error': str(e), 
                        'traceback': tb,
                        'processing_time_seconds': processing_time
                    },
                    step='process_spamcheck'
                )
                
                # Set spamcheck status to failed
                spamcheck.status = 'failed'
                await asyncio.to_thread(spamcheck.save)
                
            except Exception as log_error:
                self.stdout.write(self.style.ERROR(f"❌ Error logging error: {str(log_error)}"))
                
            return False

    async def get_ready_spamchecks(self):
        """Get spamchecks that are ready for report generation based on waiting time after in_progress status"""
        now = timezone.now()
        self.stdout.write(f"🔍 Finding spamchecks ready for report generation at {now}")
        
        try:
            # First, check how many spamchecks are currently in 'generating_reports' status
            spamchecks_in_progress_count = await asyncio.to_thread(
                lambda: UserSpamcheckBison.objects.filter(
                    status='generating_reports'
                ).count()
            )
            
            self.stdout.write(f"ℹ️ Found {spamchecks_in_progress_count} spamchecks currently in generating_reports status")
            
            # If we already have 2 or more spamchecks in progress, don't start any new ones
            if spamchecks_in_progress_count >= 2:
                self.stdout.write(f"⚠️ Already have {spamchecks_in_progress_count} spamchecks in generating_reports status. Skipping new ones.")
                return []
            
            # Calculate how many more we can process
            available_slots = 2 - spamchecks_in_progress_count
            self.stdout.write(f"ℹ️ Can process up to {available_slots} more spamchecks")
            
            # Get users who already have spamchecks in 'generating_reports' status
            users_with_active_reports = await asyncio.to_thread(
                lambda: set(UserSpamcheckBison.objects.filter(
                    status='generating_reports'
                ).values_list('user_id', flat=True))
            )
            
            self.stdout.write(f"ℹ️ Found {len(users_with_active_reports)} users with active report generation")
            
            # Get spamchecks ready for report generation, excluding users who already have active report generation
            spamchecks = await asyncio.to_thread(
                lambda: list(UserSpamcheckBison.objects.filter(
                    Q(status='waiting_for_reports') &
                    (
                        Q(reports_waiting_time__isnull=True) |  # No waiting time specified (uses default 1h)
                        Q(reports_waiting_time=0) |  # Immediate generation
                        Q(updated_at__lte=now - timedelta(hours=1), reports_waiting_time=1.0) |  # Default 1h waiting
                        Q(updated_at__lte=now - timedelta(minutes=30), reports_waiting_time=0.5) |  # 30min waiting
                        Q(updated_at__lte=now - timedelta(hours=2), reports_waiting_time=2.0) |  # 2h waiting
                        Q(updated_at__lte=now - timedelta(hours=3), reports_waiting_time=3.0) |  # 3h waiting
                        Q(updated_at__lte=now - timedelta(hours=4), reports_waiting_time=4.0) |  # 4h waiting
                        Q(updated_at__lte=now - timedelta(hours=5), reports_waiting_time=5.0) |  # 5h waiting
                        Q(updated_at__lte=now - timedelta(hours=6), reports_waiting_time=6.0) |  # 6h waiting
                        Q(updated_at__lte=now - timedelta(hours=7), reports_waiting_time=7.0) |  # 7h waiting
                        Q(updated_at__lte=now - timedelta(hours=8), reports_waiting_time=8.0) |  # 8h waiting
                        Q(updated_at__lte=now - timedelta(hours=9), reports_waiting_time=9.0) |  # 9h waiting
                        Q(updated_at__lte=now - timedelta(hours=10), reports_waiting_time=10.0) |  # 10h waiting
                        Q(updated_at__lte=now - timedelta(hours=11), reports_waiting_time=11.0) |  # 11h waiting
                        Q(updated_at__lte=now - timedelta(hours=12), reports_waiting_time=12.0)   # 12h waiting
                    )
                ).exclude(
                    user_id__in=users_with_active_reports  # Exclude users who already have active report generation
                ).select_related('user', 'user_organization').order_by('updated_at')[:available_slots])
            )
            
            if spamchecks:
                self.stdout.write(f"✅ Found {len(spamchecks)} spamchecks ready for report generation")
                for i, spamcheck in enumerate(spamchecks):
                    waiting_hours = (now - spamcheck.updated_at).total_seconds() / 3600
                    self.stdout.write(f"   {i+1}. Spamcheck ID: {spamcheck.id}, Name: {spamcheck.name}, Updated: {spamcheck.updated_at}, Waiting time: {spamcheck.reports_waiting_time}h, Has been waiting: {waiting_hours:.1f}h")
            else:
                self.stdout.write("ℹ️ No spamchecks found that match the criteria")
                
            return spamchecks
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error finding ready spamchecks: {str(e)}"))
            # Log the error
            try:
                await asyncio.to_thread(
                    SpamcheckErrorLog.objects.create,
                    error_type='unknown_error',
                    provider='system',
                    error_message=f"Error finding ready spamchecks: {str(e)}",
                    error_details={'traceback': traceback.format_exc()},
                    step='get_ready_spamchecks'
                )
            except Exception as log_error:
                self.stdout.write(self.style.ERROR(f"❌ Error logging error: {str(log_error)}"))
            
            import traceback
            self.stdout.write(self.style.ERROR(traceback.format_exc()))
            return []

    async def handle_async(self, *args, **options):
        """Async entry point"""
        now = timezone.now()
        self.stdout.write(f"🔄 Starting Bison Report Generation at {now}")

        # Get spamchecks ready for report generation
        spamchecks = await self.get_ready_spamchecks()

        if not spamchecks:
            self.stdout.write("ℹ️ No spamchecks ready for report generation")
            return

        self.stdout.write(f"📋 Found {len(spamchecks)} spamchecks ready for report generation")
        
        # Change status to generating_reports for all spamchecks at the beginning
        # This prevents other instances of the script from picking up the same spamchecks
        for spamcheck in spamchecks:
            spamcheck.status = 'generating_reports'
            await asyncio.to_thread(spamcheck.save)
            self.stdout.write(f"✅ Changed status to generating_reports for spamcheck {spamcheck.id}")

        # Process all spamchecks
        results = await asyncio.gather(*[
            self.process_spamcheck(spamcheck)
            for spamcheck in spamchecks
        ])

        # Summary
        success_count = sum(1 for r in results if r)
        self.stdout.write(f"\n✅ Report Generation Complete")
        self.stdout.write(f"📊 Successfully processed {success_count} of {len(spamchecks)} spamchecks")

    def handle(self, *args, **options):
        """Entry point for the command"""
        asyncio.run(self.handle_async(*args, **options)) 