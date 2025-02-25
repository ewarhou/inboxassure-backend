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
    UserSpamcheckBisonReport
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
                'outlook_operator': '>='
            }

        conditions = {}
        
        parts = conditions_str.lower().split('sending=')
        if len(parts) == 2:
            scores_part = parts[0]
            limits_part = parts[1]
            
            # Parse scores
            score_parts = scores_part.split('and')
            for part in score_parts:
                if 'google' in part:
                    if '>=' in part:
                        conditions['google'] = float(part.split('>=')[1])
                        conditions['google_operator'] = '>='
                    elif '<=' in part:
                        conditions['google'] = float(part.split('<=')[1])
                        conditions['google_operator'] = '<='
                    elif '>' in part:
                        conditions['google'] = float(part.split('>')[1])
                        conditions['google_operator'] = '>'
                    elif '<' in part:
                        conditions['google'] = float(part.split('<')[1])
                        conditions['google_operator'] = '<'
                elif 'outlook' in part:
                    if '>=' in part:
                        conditions['outlook'] = float(part.split('>=')[1])
                        conditions['outlook_operator'] = '>='
                    elif '<=' in part:
                        conditions['outlook'] = float(part.split('<=')[1])
                        conditions['outlook_operator'] = '<='
                    elif '>' in part:
                        conditions['outlook'] = float(part.split('>')[1])
                        conditions['outlook_operator'] = '>'
                    elif '<' in part:
                        conditions['outlook'] = float(part.split('<')[1])
                        conditions['outlook_operator'] = '<'
            
            # Parse sending limits
            if '/' in limits_part:
                good_limit, bad_limit = limits_part.split('/')
                conditions['good_limit'] = int(good_limit)
                conditions['bad_limit'] = int(bad_limit)
            else:
                conditions['good_limit'] = int(limits_part)
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
        
        self.conditions_met = google_ok and outlook_ok
        sending_limit = conditions.get('good_limit', 25) if self.conditions_met else conditions.get('bad_limit', 3)
        return self.conditions_met, sending_limit

    async def update_sending_limit(self, organization, email_id, daily_limit):
        """Update sending limit for email account in Bison"""
        try:
            url = f"{organization.base_url}/api/sender-emails/{email_id}"
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
                if 'data' in data and 'tags' in data['data']:
                    tag_names = [tag['name'] for tag in data['data']['tags']]
                    return ','.join(tag_names)
                return None
                
        except Exception:
            return None

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
            if not account.last_emailguard_tag:
                self.skipped_accounts.append(f"{account.email_account} (No EmailGuard tag)")
                return False
            
            # Get EmailGuard report
            url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{account.last_emailguard_tag}"
            headers = {
                "Authorization": f"Bearer {user_settings.emailguard_api_key}"
            }
            
            response = await asyncio.to_thread(requests.get, url, headers=headers)
            
            if response.status_code == 200:
                data = response.json()

                # Calculate scores
                google_score = self.calculate_score(data, 'Google')
                outlook_score = self.calculate_score(data, 'Microsoft')

                # Evaluate conditions and get appropriate sending limit
                is_good, sending_limit = self.evaluate_conditions(
                    account.bison_spamcheck, 
                    google_score, 
                    outlook_score
                )

                # Get account tags from Bison API
                tags_str = await self.get_account_tags(account.organization, account.email_account)

                # Process domain-based accounts if enabled
                if account.bison_spamcheck.is_domain_based:
                    campaign_domain = self.get_domain_from_email(account.email_account)
                    domain_accounts = await self.get_domain_accounts(account.bison_spamcheck, campaign_domain)
                    
                    # Create reports for all accounts in this domain
                    processed_emails = set()
                    for domain_account in domain_accounts:
                        if domain_account.email_account in processed_emails:
                            continue
                        processed_emails.add(domain_account.email_account)
                        
                        domain_tags_str = await self.get_account_tags(domain_account.organization, domain_account.email_account)
                        
                        # Create/update report
                        report, created = await asyncio.to_thread(
                            lambda: UserSpamcheckBisonReport.objects.update_or_create(
                                spamcheck_bison=account.bison_spamcheck,
                                email_account=domain_account.email_account,
                                defaults={
                                    'bison_organization': domain_account.organization,
                                    'google_pro_score': google_score,
                                    'outlook_pro_score': outlook_score,
                                    'report_link': f"https://app.emailguard.io/inbox-placement-tests/{account.last_emailguard_tag}",
                                    'is_good': is_good,
                                    'used_subject': account.bison_spamcheck.subject,
                                    'used_body': account.bison_spamcheck.body,
                                    'sending_limit': sending_limit,
                                    'tags_list': domain_tags_str,
                                    'workspace_name': domain_account.organization.bison_organization_name
                                }
                            )
                        )
                        
                        # Update sending limit
                        success = await self.update_sending_limit(domain_account.organization, domain_account.email_account, sending_limit)
                        if success:
                            self.processed_accounts.add(domain_account.email_account)
                else:
                    # Single account processing
                    report, created = await asyncio.to_thread(
                        lambda: UserSpamcheckBisonReport.objects.update_or_create(
                            spamcheck_bison=account.bison_spamcheck,
                            email_account=account.email_account,
                            defaults={
                                'bison_organization': account.organization,
                                'google_pro_score': google_score,
                                'outlook_pro_score': outlook_score,
                                'report_link': f"https://app.emailguard.io/inbox-placement-tests/{account.last_emailguard_tag}",
                                'is_good': is_good,
                                'used_subject': account.bison_spamcheck.subject,
                                'used_body': account.bison_spamcheck.body,
                                'sending_limit': sending_limit,
                                'tags_list': tags_str,
                                'workspace_name': account.organization.bison_organization_name
                            }
                        )
                    )
                    
                    # Update sending limit
                    success = await self.update_sending_limit(account.organization, account.email_account, sending_limit)
                    if success:
                        self.processed_accounts.add(account.email_account)

                return True
            else:
                self.failed_accounts.append(f"{account.email_account} (EmailGuard API failed)")
                return False

        except Exception as e:
            self.failed_accounts.append(f"{account.email_account} (Processing error)")
            return False

    async def process_spamcheck(self, spamcheck):
        """Process a single spamcheck"""
        try:
            try:
                user_settings = await self.get_user_settings(spamcheck.user)
            except UserSettings.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"❌ User settings not found for spamcheck {spamcheck.id}"))
                return False

            # Get accounts with related data prefetched
            accounts = await self.get_accounts(spamcheck)
            total_accounts = len(accounts)

            if not accounts:
                self.stdout.write(self.style.WARNING(f"⚠️ No accounts found for spamcheck {spamcheck.id}"))
                return False

            self.stdout.write(f"\n📊 Processing spamcheck {spamcheck.id}:")
            self.stdout.write(f"   Total accounts found: {total_accounts}")

            # Update status to generating_reports before processing
            spamcheck.status = 'generating_reports'
            await asyncio.to_thread(spamcheck.save)

            # For domain-based spamchecks, group accounts by domain
            if spamcheck.is_domain_based:
                # Group accounts by domain
                domain_groups = {}
                domain_scores = {}  # Store scores per domain
                
                for account in accounts:
                    domain = self.get_domain_from_email(account.email_account)
                    if domain not in domain_groups:
                        domain_groups[domain] = []
                    domain_groups[domain].append(account)

                # First, process one account per domain to get scores
                for domain, domain_accounts in domain_groups.items():
                    representative_account = domain_accounts[0]
                    
                    # Get EmailGuard report for the representative account
                    if not representative_account.last_emailguard_tag:
                        self.skipped_accounts.extend([f"{acc.email_account} (No EmailGuard tag)" for acc in domain_accounts])
                        continue

                    url = f"https://app.emailguard.io/api/v1/inbox-placement-tests/{representative_account.last_emailguard_tag}"
                    headers = {
                        "Authorization": f"Bearer {user_settings.emailguard_api_key}"
                    }
                    
                    response = await asyncio.to_thread(requests.get, url, headers=headers)
                    
                    if response.status_code == 200:
                        data = response.json()
                        google_score = self.calculate_score(data, 'Google')
                        outlook_score = self.calculate_score(data, 'Microsoft')
                        is_good, sending_limit = self.evaluate_conditions(spamcheck, google_score, outlook_score)
                        
                        # Store scores for this domain
                        domain_scores[domain] = {
                            'google_score': google_score,
                            'outlook_score': outlook_score,
                            'is_good': is_good,
                            'sending_limit': sending_limit,
                            'report_link': f"https://app.emailguard.io/inbox-placement-tests/{representative_account.last_emailguard_tag}"
                        }
                    else:
                        self.failed_accounts.extend([f"{acc.email_account} (EmailGuard API failed)" for acc in domain_accounts])
                        continue

                # Now create reports for ALL accounts using their domain's scores
                for account in accounts:
                    domain = self.get_domain_from_email(account.email_account)
                    if domain not in domain_scores:
                        continue

                    scores = domain_scores[domain]
                    tags_str = await self.get_account_tags(account.organization, account.email_account)

                    # Create/update report for each account
                    report, created = await asyncio.to_thread(
                        lambda: UserSpamcheckBisonReport.objects.update_or_create(
                            spamcheck_bison=spamcheck,
                            email_account=account.email_account,
                            defaults={
                                'bison_organization': account.organization,
                                'google_pro_score': scores['google_score'],
                                'outlook_pro_score': scores['outlook_score'],
                                'report_link': scores['report_link'],
                                'is_good': scores['is_good'],
                                'used_subject': spamcheck.subject,
                                'used_body': spamcheck.body,
                                'sending_limit': scores['sending_limit'],
                                'tags_list': tags_str,
                                'workspace_name': account.organization.bison_organization_name
                            }
                        )
                    )

                    # Update sending limit
                    success = await self.update_sending_limit(account.organization, account.email_account, scores['sending_limit'])
                    if success:
                        self.processed_accounts.add(account.email_account)
            else:
                # Process all accounts in parallel for non-domain-based spamchecks
                results = await asyncio.gather(*[
                    self.process_account(account, user_settings)
                    for account in accounts
                ])

            # Print processing summary
            processed_count = len(self.processed_accounts)
            skipped_count = len(self.skipped_accounts)
            failed_count = len(self.failed_accounts)

            self.stdout.write(f"\n📈 Processing Summary for spamcheck {spamcheck.id}:")
            self.stdout.write(f"   ✓ Successfully processed: {processed_count}")
            self.stdout.write(f"   ⚠️ Skipped: {skipped_count}")
            self.stdout.write(f"   ❌ Failed: {failed_count}")

            if skipped_count > 0:
                self.stdout.write("\n⚠️ Skipped accounts:")
                for account in self.skipped_accounts:
                    self.stdout.write(f"   - {account}")

            if failed_count > 0:
                self.stdout.write("\n❌ Failed accounts:")
                for account in self.failed_accounts:
                    self.stdout.write(f"   - {account}")

            # Only mark spamcheck as completed if we processed all accounts
            if processed_count == total_accounts:
                spamcheck.status = 'completed'
                await asyncio.to_thread(spamcheck.save)
                return True
            else:
                return False

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"❌ Error processing spamcheck {spamcheck.id}: {str(e)}"))
            return False

    async def get_ready_spamchecks(self):
        """Get spamchecks that are ready for report generation based on waiting time after in_progress status"""
        now = timezone.now()
        
        return await asyncio.to_thread(
            lambda: list(UserSpamcheckBison.objects.filter(
                Q(status='in_progress') &
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
        self.stdout.write(f"🔄 Starting Bison Report Generation at {now}")

        # Get spamchecks ready for report generation
        spamchecks = await self.get_ready_spamchecks()

        if not spamchecks:
            self.stdout.write("ℹ️ No spamchecks ready for report generation")
            return

        self.stdout.write(f"📋 Found {len(spamchecks)} spamchecks ready for report generation")

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