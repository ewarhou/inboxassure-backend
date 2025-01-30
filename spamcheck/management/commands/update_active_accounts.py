from django.core.management.base import BaseCommand
from django.db import transaction
from spamcheck.models import UserSpamcheck, UserSpamcheckAccounts
from settings.models import UserSettings, UserInstantly
import requests
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Update active accounts for recurring spamchecks by removing inactive accounts'

    def fetch_instantly_accounts(self, user_token: str, org_token: str) -> List[Dict[str, Any]]:
        """Fetch all accounts from Instantly API and filter inactive ones"""
        try:
            response = requests.post(
                "https://app.instantly.ai/backend/api/v1/account/list",
                headers={
                    "Cookie": f"__session={user_token}",
                    "X-Org-Auth": org_token,
                    "Content-Type": "application/json"
                },
                json={
                    "limit": 1000,  # High limit to fetch all accounts
                    "include_tags": True
                    # No status filter - get all accounts
                },
                timeout=30
            )
            
            if response.status_code != 200:
                self.stdout.write(self.style.ERROR(f"Failed to fetch accounts: {response.text}"))
                return []
                
            data = response.json()
            all_accounts = data.get("accounts", [])
            
            # Filter for inactive accounts (status != 1)
            inactive_accounts = [acc for acc in all_accounts if acc.get("status") != 1]
            
            # Print full account details for debugging
            self.stdout.write("\nAll accounts from Instantly:")
            self.stdout.write(f"Total accounts: {len(all_accounts)}")
            self.stdout.write(f"Inactive accounts: {len(inactive_accounts)}")
            
            self.stdout.write("\nInactive accounts details:")
            for account in inactive_accounts:
                self.stdout.write(f"Email: {account.get('email')}")
                self.stdout.write(f"Status: {account.get('status')}")
                self.stdout.write(f"Tags: {[tag.get('label') for tag in account.get('tags', [])]}")
                self.stdout.write("---")
            
            return inactive_accounts
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error fetching accounts: {str(e)}"))
            return []

    def handle(self, *args, **options):
        # Get all recurring spamchecks
        recurring_spamchecks = UserSpamcheck.objects.filter(
            recurring_days__isnull=False,
            recurring_days__gt=0
        ).select_related('user', 'user_organization')
        
        self.stdout.write(f"Found {recurring_spamchecks.count()} recurring spamchecks")
        
        # Get accounts only for recurring spamchecks
        recurring_spamcheck_ids = [s.id for s in recurring_spamchecks]
        recurring_accounts = UserSpamcheckAccounts.objects.filter(spamcheck_id__in=recurring_spamcheck_ids)
        
        self.stdout.write("\nAccounts in recurring spamchecks:")
        for acc in recurring_accounts:
            self.stdout.write(f"Email: {acc.email_account}")
            self.stdout.write(f"Spamcheck: {acc.spamcheck.name} (ID: {acc.spamcheck.id})")
            self.stdout.write(f"Organization: {acc.organization.instantly_organization_name}")
            self.stdout.write("---")
        
        total_removed = 0
        processed_spamchecks = 0
        
        for spamcheck in recurring_spamchecks:
            try:
                self.stdout.write(f"\nProcessing spamcheck {spamcheck.id} ({spamcheck.name})")
                
                # Get user settings for API tokens
                user_settings = UserSettings.objects.get(user=spamcheck.user)
                
                if not user_settings.instantly_user_token or not spamcheck.user_organization.instantly_organization_token:
                    self.stdout.write(self.style.WARNING(f"Missing tokens for spamcheck {spamcheck.id}"))
                    continue
                
                # Get inactive accounts from Instantly
                inactive_accounts = self.fetch_instantly_accounts(
                    user_settings.instantly_user_token,
                    spamcheck.user_organization.instantly_organization_token
                )
                
                inactive_emails = [account["email"] for account in inactive_accounts]
                self.stdout.write(f"\nFound {len(inactive_emails)} inactive accounts from Instantly: {inactive_emails}")
                
                # Get our DB accounts for this spamcheck
                db_accounts = UserSpamcheckAccounts.objects.filter(spamcheck=spamcheck)
                db_emails = [acc.email_account for acc in db_accounts]
                self.stdout.write(f"\nFound {len(db_emails)} accounts in spamcheck {spamcheck.name}: {db_emails}")
                
                # Find accounts that are both in our DB and inactive in Instantly
                accounts_to_remove = []
                for db_account in db_accounts:
                    if db_account.email_account in inactive_emails:
                        accounts_to_remove.append(db_account)
                        self.stdout.write(f"\nAccount {db_account.email_account} is inactive in Instantly and exists in our DB")
                
                if accounts_to_remove:
                    with transaction.atomic():
                        # Delete inactive accounts
                        for account in accounts_to_remove:
                            account.delete()
                            self.stdout.write(self.style.SUCCESS(f"Removed inactive account: {account.email_account}"))
                        total_removed += len(accounts_to_remove)
                else:
                    self.stdout.write("\nNo inactive accounts found in this spamcheck")
                
                processed_spamchecks += 1
                
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error processing spamcheck {spamcheck.id}: {str(e)}"))
                continue
        
        self.stdout.write(
            self.style.SUCCESS(
                f"\nSummary: Processed {processed_spamchecks} spamchecks and removed {total_removed} inactive accounts"
            )
        ) 