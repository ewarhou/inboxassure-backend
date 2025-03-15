import time
import requests
import json
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from spamcheck.models import UserSpamcheckBison
from settings.api import log_to_terminal
from .models import UserCampaignsBison, UserBisonDashboardSummary
from django.utils import timezone
from django.db.models import Count, Avg, Sum, F, Q
from datetime import datetime, timedelta
from analytics.models import UserBisonProviderPerformance, UserBisonSendingPower

@receiver(post_save, sender=UserSpamcheckBison)
def handle_spamcheck_status_change(sender, instance, created, **kwargs):
    """
    Signal handler to detect when a Bison spamcheck status changes to "completed".
    When this happens, we update the UserCampaignsBison table with fresh data.
    """
    # Skip if this is a new record being created
    if created:
        return
    
    # Get the update_fields from kwargs to check if status was updated
    update_fields = kwargs.get('update_fields')
    
    # If update_fields is provided and 'status' is not in it, skip processing
    if update_fields is not None and 'status' not in update_fields:
        log_to_terminal("BisonCampaigns", "Signal", f"Skipping API calls - status field not updated for spamcheck: {instance.name} (ID: {instance.id})")
        return
        
    # Check if status is now "completed"
    if instance.status == "completed":
        log_to_terminal("BisonCampaigns", "Signal", f"Detected completed spamcheck: {instance.name} (ID: {instance.id})")
        # Call function to update campaigns table
        update_bison_campaigns_table(instance)
        # Call function to update dashboard summary
        update_bison_dashboard_summary(instance)
        # Call function to update provider performance
        update_bison_provider_performance(instance)
        # Call function to update sending power
        update_bison_sending_power(instance)
    else:
        log_to_terminal("BisonCampaigns", "Signal", f"Spamcheck status is {instance.status}, not triggering API calls")

def update_bison_campaigns_table(spamcheck):
    """
    Updates the UserCampaignsBison table with fresh data from Bison API
    and the latest metrics from our database.
    
    Args:
        spamcheck: The UserSpamcheckBison instance that was just completed
    """
    start_time = time.time()
    
    user = spamcheck.user
    org = spamcheck.user_organization
    
    log_to_terminal("BisonCampaigns", "Update", f"Updating campaigns for user {user.email}, org: {org.bison_organization_name}")
    log_to_terminal("BisonCampaigns", "Debug", f"Organization ID: {org.id}, API Key: {org.bison_organization_api_key[:5]}...")
    
    try:
        # 1. Get all accounts from Bison API
        accounts_start_time = time.time()
        all_bison_accounts = []
        current_page = 1
        per_page = 100  # Use a larger page size for efficiency
        
        while True:
            api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
            log_to_terminal("BisonCampaigns", "Debug", f"Fetching accounts from: {api_url}, page: {current_page}")
            
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {org.bison_organization_api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "page": current_page,
                    "per_page": per_page
                }
            )
            
            if response.status_code != 200:
                log_to_terminal("BisonCampaigns", "Error", f"API Error: {response.text}")
                break
                
            data = response.json()
            accounts = data.get('data', [])
            log_to_terminal("BisonCampaigns", "Debug", f"Page {current_page}: Found {len(accounts)} accounts")
            
            # Debug: Print first account details
            if accounts and current_page == 1:
                first_account = accounts[0]
                log_to_terminal("BisonCampaigns", "Debug", f"Sample account: {json.dumps(first_account)}")
            
            all_bison_accounts.extend(accounts)
            
            # Check if we've reached the last page
            total_pages = data.get('meta', {}).get('last_page', 1)
            if current_page >= total_pages:
                break
                
            current_page += 1
        
        accounts_time = time.time() - accounts_start_time
        log_to_terminal("BisonCampaigns", "Accounts", f"Found total {len(all_bison_accounts)} accounts in Bison (took {accounts_time:.2f}s)")
        
        # Deduplicate accounts by email to handle Bison API duplicate issue
        dedup_start_time = time.time()
        unique_accounts = {}
        for account in all_bison_accounts:
            email = account.get('email')
            if email and email not in unique_accounts:
                unique_accounts[email] = account
        
        unique_account_ids = [account.get('id') for account in unique_accounts.values()]
        dedup_time = time.time() - dedup_start_time
        log_to_terminal("BisonCampaigns", "Accounts", f"Found {len(unique_account_ids)} unique accounts (took {dedup_time:.2f}s)")
        
        # 2. Get campaigns for each account
        campaigns_start_time = time.time()
        account_campaigns = {}
        
        # Process accounts in batches to avoid making too many API calls
        batch_size = 10
        account_batches = [unique_account_ids[i:i + batch_size] for i in range(0, len(unique_account_ids), batch_size)]
        
        log_to_terminal("BisonCampaigns", "Debug", f"Processing {len(account_batches)} batches of accounts")
        
        for batch_idx, batch in enumerate(account_batches):
            log_to_terminal("BisonCampaigns", "Debug", f"Processing batch {batch_idx+1}/{len(account_batches)}")
            for account_id in batch:
                try:
                    api_url = f"{org.base_url.rstrip('/')}/api/sender-emails/{account_id}/campaigns"
                    log_to_terminal("BisonCampaigns", "Debug", f"Fetching campaigns for account {account_id} from: {api_url}")
                    
                    response = requests.get(
                        api_url,
                        headers={
                            "Authorization": f"Bearer {org.bison_organization_api_key}",
                            "Content-Type": "application/json"
                        }
                    )
                    
                    if response.status_code != 200:
                        log_to_terminal("BisonCampaigns", "Error", f"Error fetching campaigns for account {account_id}: {response.text}")
                        continue
                    
                    campaigns_data = response.json().get('data', [])
                    log_to_terminal("BisonCampaigns", "Debug", f"Account {account_id}: Found {len(campaigns_data)} campaigns")
                    
                    # Debug: Print first campaign details if available
                    if campaigns_data and batch_idx == 0 and account_id == batch[0]:
                        first_campaign = campaigns_data[0]
                        log_to_terminal("BisonCampaigns", "Debug", f"Sample campaign: {json.dumps(first_campaign)}")
                    
                    for campaign in campaigns_data:
                        campaign_id = campaign.get('id')
                        if campaign_id not in account_campaigns:
                            account_campaigns[campaign_id] = {
                                'campaign': campaign,
                                'accounts': []
                            }
                        account_campaigns[campaign_id]['accounts'].append(account_id)
                except Exception as e:
                    log_to_terminal("BisonCampaigns", "Error", f"Error processing account {account_id}: {str(e)}")
                    continue
        
        campaigns_time = time.time() - campaigns_start_time
        log_to_terminal("BisonCampaigns", "Campaigns", f"Found {len(account_campaigns)} unique campaigns (took {campaigns_time:.2f}s)")
        
        # 3. Get all campaigns to get additional details
        details_start_time = time.time()
        
        all_campaigns_data = []
        current_page = 1
        per_page = 100  # Use a larger page size for efficiency
        
        while True:
            api_url = f"{org.base_url.rstrip('/')}/api/campaigns"
            log_to_terminal("BisonCampaigns", "Debug", f"Fetching campaigns from: {api_url}, page: {current_page}")
            
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {org.bison_organization_api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "page": current_page,
                    "per_page": per_page
                }
            )
            
            if response.status_code != 200:
                log_to_terminal("BisonCampaigns", "Error", f"Error fetching campaigns: {response.text}")
                break
                
            data = response.json()
            campaigns = data.get('data', [])
            log_to_terminal("BisonCampaigns", "Debug", f"Page {current_page}: Found {len(campaigns)} campaigns")
            
            # Debug: Print first campaign details
            if campaigns and current_page == 1:
                first_campaign = campaigns[0]
                log_to_terminal("BisonCampaigns", "Debug", f"Sample campaign details: {json.dumps(first_campaign)}")
            
            all_campaigns_data.extend(campaigns)
            
            # Check if we've reached the last page
            total_pages = data.get('meta', {}).get('last_page', 1)
            if current_page >= total_pages:
                break
                
            current_page += 1
        
        log_to_terminal("BisonCampaigns", "Debug", f"API returned {len(all_campaigns_data)} campaigns in total")
        campaigns_details = {campaign.get('id'): campaign for campaign in all_campaigns_data}
        
        details_time = time.time() - details_start_time
        log_to_terminal("BisonCampaigns", "Details", f"Fetched details for {len(campaigns_details)} campaigns (took {details_time:.2f}s)")
        
        # Debug: Check for campaigns that are in account_campaigns but not in campaigns_details
        missing_campaigns = []
        for campaign_id in account_campaigns:
            if campaign_id not in campaigns_details:
                campaign_name = account_campaigns[campaign_id]['campaign'].get('name', 'Unknown')
                missing_campaigns.append((campaign_id, campaign_name))
                
        if missing_campaigns:
            log_to_terminal("BisonCampaigns", "Warning", f"Found {len(missing_campaigns)} campaigns associated with accounts but not in global campaigns list:")
            for campaign_id, campaign_name in missing_campaigns:
                log_to_terminal("BisonCampaigns", "Warning", f"Missing campaign: {campaign_name} (ID: {campaign_id})")
                # Add these missing campaigns to campaigns_details
                campaigns_details[campaign_id] = account_campaigns[campaign_id]['campaign']
                log_to_terminal("BisonCampaigns", "Debug", f"Added missing campaign {campaign_id} to campaigns_details")
        
        # 4. Get latest reports for these accounts to calculate scores
        scores_start_time = time.time()
        account_emails = [account.get('email') for account in unique_accounts.values()]
        
        # Process emails in chunks to avoid too many SQL parameters
        chunk_size = 500
        email_chunks = [account_emails[i:i + chunk_size] for i in range(0, len(account_emails), chunk_size)]
        
        log_to_terminal("BisonCampaigns", "Debug", f"Processing {len(email_chunks)} chunks of emails for scores")
        
        account_scores = {}
        for chunk_idx, chunk in enumerate(email_chunks):
            log_to_terminal("BisonCampaigns", "Debug", f"Processing email chunk {chunk_idx+1}/{len(email_chunks)}")
            
            placeholders = ','.join(['%s'] * len(chunk))
            query = f"""
            WITH latest_reports AS (
                SELECT 
                    *,
                    ROW_NUMBER() OVER (
                        PARTITION BY email_account 
                        ORDER BY created_at DESC
                    ) as rn
                FROM user_spamcheck_bison_reports
                WHERE bison_organization_id = %s
                AND email_account IN ({placeholders})
            )
            SELECT 
                email_account,
                google_pro_score,
                outlook_pro_score,
                sending_limit
            FROM latest_reports
            WHERE rn = 1
            """
            
            with connection.cursor() as cursor:
                cursor.execute(query, [org.id] + chunk)
                results = cursor.fetchall()
                
                log_to_terminal("BisonCampaigns", "Debug", f"Found {len(results)} score results for chunk {chunk_idx+1}")
                
                for result in results:
                    email, google_score, outlook_score, sending_limit = result
                    account_scores[email] = {
                        'google_score': float(google_score) * 100 if google_score is not None else 0,
                        'outlook_score': float(outlook_score) * 100 if outlook_score is not None else 0,
                        'sending_limit': sending_limit or 25
                    }
                    
                    # Debug: Print some sample scores
                    if chunk_idx == 0 and len(account_scores) <= 3:
                        log_to_terminal("BisonCampaigns", "Debug", f"Score for {email}: Google={account_scores[email]['google_score']}, Outlook={account_scores[email]['outlook_score']}, Limit={account_scores[email]['sending_limit']}")
        
        scores_time = time.time() - scores_start_time
        log_to_terminal("BisonCampaigns", "Scores", f"Fetched scores for {len(account_scores)} accounts (took {scores_time:.2f}s)")
        
        # 5. Update or create campaign records in the database
        update_start_time = time.time()
        
        # First, get all existing campaigns for this organization
        existing_campaigns = UserCampaignsBison.objects.filter(
            user=user,
            bison_organization=org
        )
        existing_campaign_ids = {camp.campaign_id: camp for camp in existing_campaigns}
        
        log_to_terminal("BisonCampaigns", "Debug", f"Found {len(existing_campaign_ids)} existing campaigns in database")
        
        # Track which campaign IDs we've processed
        processed_campaign_ids = set()
        
        # First, process campaigns from account_campaigns
        for campaign_id, data in account_campaigns.items():
            campaign = data['campaign']
            accounts = data['accounts']
            campaign_details = campaigns_details.get(campaign_id, {})
            
            log_to_terminal("BisonCampaigns", "Debug", f"Processing campaign from accounts: {campaign.get('name', '')} (ID: {campaign_id})")
            
            # Get emails for these accounts
            campaign_account_emails = []
            for account_id in accounts:
                for account in all_bison_accounts:
                    if account.get('id') == account_id:
                        email = account.get('email')
                        if email:
                            campaign_account_emails.append(email)
            
            log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has {len(campaign_account_emails)} connected emails")
            
            # Calculate average scores
            google_scores = [account_scores.get(email, {}).get('google_score', 0) for email in campaign_account_emails]
            outlook_scores = [account_scores.get(email, {}).get('outlook_score', 0) for email in campaign_account_emails]
            
            # Debug: Print raw scores for campaigns with zero scores
            if not google_scores or not outlook_scores or min(google_scores) == 0 or min(outlook_scores) == 0:
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has potential zero scores")
                log_to_terminal("BisonCampaigns", "Debug", f"Google scores: {google_scores[:10]}")
                log_to_terminal("BisonCampaigns", "Debug", f"Outlook scores: {outlook_scores[:10]}")
                
                # Print which emails have zero scores
                for email in campaign_account_emails:
                    g_score = account_scores.get(email, {}).get('google_score', 0)
                    o_score = account_scores.get(email, {}).get('outlook_score', 0)
                    if g_score == 0 or o_score == 0:
                        log_to_terminal("BisonCampaigns", "Debug", f"Email with zero score: {email}, Google: {g_score}, Outlook: {o_score}")
            
            # Check if we have any accounts with scores
            has_accounts_with_scores = any(email in account_scores for email in campaign_account_emails)
            
            # Debug: Print account scores information
            log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has {len(campaign_account_emails)} connected emails")
            log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has {len([email for email in campaign_account_emails if email in account_scores])} emails with scores in our database")
            log_to_terminal("BisonCampaigns", "Debug", f"has_accounts_with_scores = {has_accounts_with_scores}")
            
            # Additional check for zero scores
            all_zero_scores = False
            if google_scores and outlook_scores:
                if all(score == 0 for score in google_scores) and all(score == 0 for score in outlook_scores):
                    all_zero_scores = True
                    log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has ALL ZERO SCORES. Will set sends_per_account to NULL")
            
            # If all scores are zero, treat it the same as having no accounts with scores
            if all_zero_scores:
                has_accounts_with_scores = False
            
            if has_accounts_with_scores:
                avg_google_score = sum(google_scores) / len(google_scores) if google_scores else 0
                avg_outlook_score = sum(outlook_scores) / len(outlook_scores) if outlook_scores else 0
                
                # Calculate average sending limit of connected accounts
                sending_limits = [account_scores.get(email, {}).get('sending_limit', 25) for email in campaign_account_emails if email in account_scores]
                avg_sending_limit = round(sum(sending_limits) / len(sending_limits)) if sending_limits else 25
                
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} average scores: Google={avg_google_score:.2f}, Outlook={avg_outlook_score:.2f}")
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} max daily sends: {campaign.get('max_emails_per_day', 0)}, avg sending limit: {avg_sending_limit}")
            else:
                # No accounts with scores found, set to 0 (not NULL)
                avg_google_score = 0
                avg_outlook_score = 0
                avg_sending_limit = 0  # Changed from None to 0
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has NO accounts with scores in our database. Setting sends_per_account to 0")
            
            # Get max daily sends
            max_daily_sends = campaign.get('max_emails_per_day', 0)
            
            # Update or create the campaign record
            if campaign_id in existing_campaign_ids:
                # Update existing record
                camp_obj = existing_campaign_ids[campaign_id]
                log_to_terminal("BisonCampaigns", "Debug", f"Updating existing campaign {campaign_id} in database")
                
                # Log previous values
                log_to_terminal("BisonCampaigns", "Debug", f"Previous values - Google: {camp_obj.google_score}, Outlook: {camp_obj.outlook_score}, Connected: {camp_obj.connected_emails_count}, Sends per account: {camp_obj.sends_per_account}")
                
                camp_obj.campaign_name = campaign.get('name', '')
                camp_obj.connected_emails_count = len(accounts)
                camp_obj.sends_per_account = avg_sending_limit  # This will be None if no accounts with scores
                camp_obj.google_score = round(avg_google_score, 2)
                camp_obj.outlook_score = round(avg_outlook_score, 2)
                camp_obj.max_daily_sends = max_daily_sends
                camp_obj.save()
                
                # Log updated values for verification
                log_to_terminal("BisonCampaigns", "Debug", f"Updated values - Google: {camp_obj.google_score}, Outlook: {camp_obj.outlook_score}, Connected: {camp_obj.connected_emails_count}, Sends per account: {camp_obj.sends_per_account}")
                log_to_terminal("BisonCampaigns", "Update", f"Updated campaign: {camp_obj.campaign_name} (ID: {campaign_id})")
            else:
                # Create new record
                log_to_terminal("BisonCampaigns", "Debug", f"Creating new campaign {campaign_id} in database")
                new_campaign = UserCampaignsBison.objects.create(
                    user=user,
                    bison_organization=org,
                    campaign_id=campaign_id,
                    campaign_name=campaign.get('name', ''),
                    connected_emails_count=len(accounts),
                    sends_per_account=avg_sending_limit,  # This will be None if no accounts with scores
                    google_score=round(avg_google_score, 2),
                    outlook_score=round(avg_outlook_score, 2),
                    max_daily_sends=max_daily_sends
                )
                log_to_terminal("BisonCampaigns", "Debug", f"Created campaign with sends_per_account: {new_campaign.sends_per_account}")
                log_to_terminal("BisonCampaigns", "Create", f"Created campaign: {campaign.get('name', '')} (ID: {campaign_id})")
            
            processed_campaign_ids.add(campaign_id)
        
        # Now process any campaigns from campaigns_details that weren't in account_campaigns
        for campaign_id, campaign in campaigns_details.items():
            if campaign_id in processed_campaign_ids:
                continue  # Skip campaigns we've already processed
                
            log_to_terminal("BisonCampaigns", "Debug", f"Processing campaign from global list: {campaign.get('name', '')} (ID: {campaign_id})")
            
            # For campaigns not in account_campaigns, we don't have account information
            # So we'll set default values
            
            # Update or create the campaign record
            if campaign_id in existing_campaign_ids:
                # Update existing record
                camp_obj = existing_campaign_ids[campaign_id]
                log_to_terminal("BisonCampaigns", "Debug", f"Updating existing campaign {campaign_id} in database")
                
                # Only update the name and max_daily_sends, keep other values
                camp_obj.campaign_name = campaign.get('name', '')
                camp_obj.max_daily_sends = campaign.get('max_emails_per_day', 0)
                camp_obj.save()
                
                log_to_terminal("BisonCampaigns", "Update", f"Updated campaign name: {camp_obj.campaign_name} (ID: {campaign_id})")
            else:
                # Create new record with default values
                log_to_terminal("BisonCampaigns", "Debug", f"Creating new campaign {campaign_id} in database (no account data)")
                new_campaign = UserCampaignsBison.objects.create(
                    user=user,
                    bison_organization=org,
                    campaign_id=campaign_id,
                    campaign_name=campaign.get('name', ''),
                    connected_emails_count=0,  # No connected emails known
                    sends_per_account=0,       # Default value
                    google_score=0,            # Default value
                    outlook_score=0,           # Default value
                    max_daily_sends=campaign.get('max_emails_per_day', 0)
                )
                log_to_terminal("BisonCampaigns", "Create", f"Created campaign with default values: {campaign.get('name', '')} (ID: {campaign_id})")
            
            processed_campaign_ids.add(campaign_id)
        
        # Check for campaigns in reports that aren't in our processed list
        try:
            with connection.cursor() as cursor:
                query = """
                SELECT DISTINCT campaign_id, campaign_name
                FROM user_spamcheck_bison_reports
                WHERE bison_organization_id = %s
                AND campaign_id IS NOT NULL
                AND campaign_id != ''
                """
                cursor.execute(query, [org.id])
                report_campaigns = cursor.fetchall()
                
                if report_campaigns:
                    log_to_terminal("BisonCampaigns", "Debug", f"Found {len(report_campaigns)} campaigns in reports table")
                    
                    for row in report_campaigns:
                        campaign_id, campaign_name = row
                        
                        # Skip if we've already processed this campaign
                        if campaign_id in processed_campaign_ids:
                            continue
                        
                        log_to_terminal("BisonCampaigns", "Warning", f"Found campaign in reports but not in API: {campaign_name} (ID: {campaign_id})")
                        
                        # Create a record for this campaign if it doesn't exist
                        if campaign_id not in existing_campaign_ids:
                            log_to_terminal("BisonCampaigns", "Debug", f"Creating campaign from reports: {campaign_name} (ID: {campaign_id})")
                            new_campaign = UserCampaignsBison.objects.create(
                                user=user,
                                bison_organization=org,
                                campaign_id=campaign_id,
                                campaign_name=campaign_name or f"Campaign {campaign_id}",
                                connected_emails_count=0,  # Will be updated in future runs
                                sends_per_account=0,       # Default value
                                google_score=0,            # Default value
                                outlook_score=0,           # Default value
                                max_daily_sends=0          # Default value
                            )
                            log_to_terminal("BisonCampaigns", "Create", f"Created campaign from reports: {campaign_name} (ID: {campaign_id})")
                            processed_campaign_ids.add(campaign_id)
        except Exception as e:
            log_to_terminal("BisonCampaigns", "Error", f"Error checking reports for campaigns: {str(e)}")
        
        # Delete campaigns that no longer exist
        for camp_id, camp_obj in existing_campaign_ids.items():
            if camp_id not in processed_campaign_ids:
                log_to_terminal("BisonCampaigns", "Delete", f"Deleting campaign: {camp_obj.campaign_name} (ID: {camp_id})")
                camp_obj.delete()
        
        update_time = time.time() - update_start_time
        log_to_terminal("BisonCampaigns", "Update", f"Updated {len(processed_campaign_ids)} campaigns (took {update_time:.2f}s)")
        
        total_time = time.time() - start_time
        log_to_terminal("BisonCampaigns", "Complete", f"Total execution time: {total_time:.2f}s")
        
    except Exception as e:
        import traceback
        log_to_terminal("BisonCampaigns", "Error", f"Error updating campaigns for organization {org.bison_organization_name}: {str(e)}")
        log_to_terminal("BisonCampaigns", "Error", f"Traceback: {traceback.format_exc()}")

def update_bison_dashboard_summary(spamcheck):
    """
    Creates a new dashboard summary record when a spam check is completed.
    """
    user = spamcheck.user
    org = spamcheck.user_organization
    
    log_to_terminal("Signals", "DashboardSummary", f"Updating dashboard summary for {org.bison_organization_name}")
    
    try:
        # 1. Get ALL accounts from Bison API by paginating until we have everything
        api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
        
        all_bison_accounts = []
        current_page = 1
        per_page = 100  # Use a larger page size for efficiency
        
        while True:
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {org.bison_organization_api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "page": current_page,
                    "per_page": per_page
                }
            )
            
            if response.status_code != 200:
                log_to_terminal("Signals", "DashboardSummary", f"API Error: {response.text}")
                break
                
            data = response.json()
            accounts = data.get('data', [])
            all_bison_accounts.extend(accounts)
            
            # Check if we've reached the last page
            total_pages = data.get('meta', {}).get('last_page', 1)
            if current_page >= total_pages:
                break
                
            current_page += 1
        
        log_to_terminal("Signals", "DashboardSummary", f"Found {len(all_bison_accounts)} accounts for {org.bison_organization_name}")
        
        # Deduplicate accounts by email to handle Bison API duplicate issue
        unique_emails = {}
        for account in all_bison_accounts:
            email = account.get('email')
            if email and email not in unique_emails:
                unique_emails[email] = account
        
        bison_emails = list(unique_emails.keys())
        
        if not bison_emails:
            log_to_terminal("Signals", "DashboardSummary", f"No valid email addresses found for {org.bison_organization_name}")
            return
        
        # 2. Get reports for these accounts
        with connection.cursor() as cursor:
            # Process emails in chunks to avoid too many SQL parameters
            chunk_size = 500
            email_chunks = [bison_emails[i:i + chunk_size] for i in range(0, len(bison_emails), chunk_size)]
            
            # Initialize counters
            checked_accounts = 0
            at_risk_accounts = 0
            protected_accounts = 0
            
            # Initialize score sums
            google_score_sum = 0.0
            outlook_score_sum = 0.0
            
            # Initialize sending limit sum
            sending_limit_sum = 0
            
            for chunk in email_chunks:
                placeholders = ','.join(['%s'] * len(chunk))
                query = f"""
                SELECT 
                    ubr.email_account,
                    ubr.google_pro_score,
                    ubr.outlook_pro_score,
                    ubr.is_good,
                    ubr.sending_limit
                FROM user_spamcheck_bison_reports ubr
                WHERE ubr.bison_organization_id = %s
                AND ubr.email_account IN ({placeholders})
                ORDER BY ubr.created_at DESC
                """
                cursor.execute(query, [org.id] + chunk)
                rows = cursor.fetchall()
                
                # Use a set to track unique accounts
                processed_accounts = set()
                
                for row in rows:
                    email_account, google_score, outlook_score, is_good, sending_limit = row
                    
                    # Skip if we've already processed this account
                    if email_account in processed_accounts:
                        continue
                    
                    processed_accounts.add(email_account)
                    checked_accounts += 1
                    
                    # Add to score sums
                    google_score_sum += float(google_score or 0.0)
                    outlook_score_sum += float(outlook_score or 0.0)
                    
                    # Count at-risk and protected accounts
                    if is_good:
                        protected_accounts += 1
                        sending_limit_sum += (sending_limit or 25)  # Default to 25 if not specified
                    else:
                        at_risk_accounts += 1
        
        # Calculate averages and percentages
        avg_google_score = google_score_sum / checked_accounts if checked_accounts > 0 else 0
        avg_outlook_score = outlook_score_sum / checked_accounts if checked_accounts > 0 else 0
        
        # Calculate email counts and percentages
        avg_score = (avg_google_score + avg_outlook_score) / 2
        
        # Assuming each account sends up to its sending limit
        total_emails = sending_limit_sum
        
        # Calculate spam vs inbox percentages
        spam_percentage = (1 - avg_score) * 100 if avg_score <= 1 else 0
        inbox_percentage = avg_score * 100 if avg_score <= 1 else 100
        
        # Calculate email counts
        spam_emails = int(total_emails * (spam_percentage / 100))
        inbox_emails = int(total_emails * (inbox_percentage / 100))
        
        # Create a new dashboard summary record
        summary = UserBisonDashboardSummary.objects.create(
            user=user,
            bison_organization=org,
            checked_accounts=checked_accounts,
            at_risk_accounts=at_risk_accounts,
            protected_accounts=protected_accounts,
            spam_emails_count=spam_emails,
            inbox_emails_count=inbox_emails,
            spam_emails_percentage=spam_percentage,
            inbox_emails_percentage=inbox_percentage,
            overall_deliverability=avg_score * 100  # Convert to percentage
        )
        
        log_to_terminal("Signals", "DashboardSummary", f"Created dashboard summary for {org.bison_organization_name}")
        
    except Exception as e:
        log_to_terminal("Signals", "DashboardSummary", f"Error updating dashboard summary: {str(e)}")

def update_bison_provider_performance(spamcheck):
    """
    Creates new provider performance records when a spam check is completed.
    """
    user = spamcheck.user
    org = spamcheck.user_organization
    
    log_to_terminal("Signals", "ProviderPerformance", f"Updating provider performance for {org.bison_organization_name}")
    
    try:
        # Set date range for the last 30 days
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=30)
        
        # 1. Get ALL accounts from Bison API by paginating until we have everything
        api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
        
        all_bison_accounts = []
        current_page = 1
        per_page = 100  # Use a larger page size for efficiency
        
        while True:
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {org.bison_organization_api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "page": current_page,
                    "per_page": per_page
                }
            )
            
            if response.status_code != 200:
                log_to_terminal("Signals", "ProviderPerformance", f"API Error: {response.text}")
                break
                
            data = response.json()
            accounts = data.get('data', [])
            all_bison_accounts.extend(accounts)
            
            # Check if we've reached the last page
            total_pages = data.get('meta', {}).get('last_page', 1)
            if current_page >= total_pages:
                break
                
            current_page += 1
        
        log_to_terminal("Signals", "ProviderPerformance", f"Found {len(all_bison_accounts)} accounts for {org.bison_organization_name}")
        
        # Deduplicate accounts by email to handle Bison API duplicate issue
        unique_emails = {}
        for account in all_bison_accounts:
            email = account.get('email')
            if email and email not in unique_emails:
                unique_emails[email] = account
        
        # Create a mapping of provider tags
        provider_tags = {}
        for email, account in unique_emails.items():
            tags = account.get('tags', [])
            for tag in tags:
                tag_name = tag.get('name', '')
                # Look for provider tags that start with "p."
                if tag_name.startswith('p.'):
                    provider_tags[email] = tag_name[2:]  # Remove the "p." prefix
        
        log_to_terminal("Signals", "ProviderPerformance", f"Found {len(provider_tags)} accounts with provider tags")
        
        if not provider_tags:
            log_to_terminal("Signals", "ProviderPerformance", f"No accounts with provider tags found for {org.bison_organization_name}")
            return
        
        # Helper function to create a new stats dictionary
        def new_stats_dict():
            return {
                'accounts': set(),  # Use set to count unique accounts
                'good_accounts': 0,
                'google_sum': 0.0,
                'outlook_sum': 0.0,
                'sending_power': 0,
                'emails_sent': 0,
                'bounced': 0,
                'replied': 0
            }
        
        # Structure: {provider: {date: stats}}
        from collections import defaultdict
        daily_stats = defaultdict(lambda: defaultdict(new_stats_dict))
        
        # 2. Get reports for these accounts
        with connection.cursor() as cursor:
            # Get emails with provider tags
            tagged_emails = list(provider_tags.keys())
            
            # Process emails in chunks to avoid too many SQL parameters
            chunk_size = 500
            email_chunks = [tagged_emails[i:i + chunk_size] for i in range(0, len(tagged_emails), chunk_size)]
            
            for chunk in email_chunks:
                placeholders = ','.join(['%s'] * len(chunk))
                query = f"""
                SELECT 
                    ubr.email_account,
                    ubr.google_pro_score,
                    ubr.outlook_pro_score,
                    ubr.is_good,
                    ubr.sending_limit,
                    ubr.emails_sent_count,
                    ubr.bounced_count,
                    ubr.unique_replied_count,
                    DATE(ubr.created_at) as report_date
                FROM user_spamcheck_bison_reports ubr
                WHERE ubr.bison_organization_id = %s
                AND ubr.email_account IN ({placeholders})
                AND DATE(ubr.created_at) BETWEEN %s AND %s
                ORDER BY ubr.created_at DESC
                """
                cursor.execute(query, [org.id] + chunk + [start_date.isoformat(), end_date.isoformat()])
                rows = cursor.fetchall()
                
                # Process each row and aggregate by day and provider
                for row in rows:
                    email_account, google_score, outlook_score, is_good, sending_limit, emails_sent, bounced, replied, report_date = row
                    
                    # Skip if this account doesn't have a provider tag
                    if email_account not in provider_tags:
                        continue
                    
                    provider_name = provider_tags[email_account]
                    sending_limit = sending_limit or 25  # Default sending limit if not specified
                    
                    # Update daily stats
                    stats = daily_stats[provider_name][report_date]
                    
                    if email_account not in stats['accounts']:
                        stats['accounts'].add(email_account)
                        # Only add scores for new accounts to avoid double counting
                        stats['google_sum'] += float(google_score or 0.0)
                        stats['outlook_sum'] += float(outlook_score or 0.0)
                        if is_good:
                            stats['good_accounts'] += 1
                            stats['sending_power'] += sending_limit
                        # Add the new metrics
                        stats['emails_sent'] += int(emails_sent or 0)
                        stats['bounced'] += int(bounced or 0)
                        stats['replied'] += int(replied or 0)
        
        # Calculate period averages for each provider
        for provider_name, daily_data in daily_stats.items():
            # Initialize period totals
            period_accounts = set()  # Track unique accounts across period
            daily_scores = {  # Track daily averages
                'google': [],
                'outlook': [],
                'sending_power': [],
                'emails_sent': [],
                'bounced': [],
                'replied': []
            }
            
            # Iterate through each day in the period
            current_dt = start_date
            while current_dt <= end_date:
                if current_dt in daily_data:
                    stats = daily_data[current_dt]
                    period_accounts.update(stats['accounts'])
                    
                    # Calculate daily averages
                    accounts_count = len(stats['accounts'])
                    if accounts_count > 0:
                        # Scores are already summed correctly per day, just divide by accounts
                        google_score = stats['google_sum'] / accounts_count
                        outlook_score = stats['outlook_sum'] / accounts_count
                        # Ensure scores are within valid range (0-1 for Bison)
                        daily_scores['google'].append(min(1.0, max(0.0, google_score)))
                        daily_scores['outlook'].append(min(1.0, max(0.0, outlook_score)))
                        daily_scores['sending_power'].append(stats['sending_power'])
                        # Add the new metrics
                        daily_scores['emails_sent'].append(stats['emails_sent'])
                        daily_scores['bounced'].append(stats['bounced'])
                        daily_scores['replied'].append(stats['replied'])
                
                current_dt += timedelta(days=1)
            
            # Calculate period averages
            if daily_scores['google']:  # If we have any data
                avg_google = round(sum(daily_scores['google']) / len(daily_scores['google']), 2)
                avg_outlook = round(sum(daily_scores['outlook']) / len(daily_scores['outlook']), 2)
                avg_sending_power = round(sum(daily_scores['sending_power']) / len(daily_scores['sending_power']))
                overall_score = round((avg_google + avg_outlook) / 2, 2)
                
                # Calculate totals for the new metrics
                total_emails_sent = sum(daily_scores['emails_sent'])
                total_bounced = sum(daily_scores['bounced'])
                total_replied = sum(daily_scores['replied'])
                
                # Create a new provider performance record
                UserBisonProviderPerformance.objects.create(
                    user=user,
                    bison_organization=org,
                    provider_name=provider_name,
                    start_date=start_date,
                    end_date=end_date,
                    total_accounts=len(period_accounts),
                    google_score=avg_google,
                    outlook_score=avg_outlook,
                    overall_score=overall_score,
                    sending_power=avg_sending_power,
                    emails_sent_count=total_emails_sent,
                    bounced_count=total_bounced,
                    unique_replied_count=total_replied
                )
                
                log_to_terminal("Signals", "ProviderPerformance", f"Created provider performance record for {provider_name} in {org.bison_organization_name}")
    
    except Exception as e:
        log_to_terminal("Signals", "ProviderPerformance", f"Error updating provider performance: {str(e)}")

def update_bison_sending_power(spamcheck):
    """
    Creates new sending power records when a spam check is completed.
    Uses the spamcheck completion date and calculates sending power for all accounts.
    """
    user = spamcheck.user
    org = spamcheck.user_organization
    
    # Use spamcheck completion date
    report_date = spamcheck.updated_at.date()
    
    log_to_terminal("Signals", "SendingPower", f"Updating sending power for {org.bison_organization_name} on {report_date}")
    
    try:
        # 1. Get ALL accounts from Bison API by paginating until we have everything
        api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
        
        all_bison_accounts = []
        current_page = 1
        per_page = 100  # Use a larger page size for efficiency
        
        while True:
            response = requests.get(
                api_url,
                headers={
                    "Authorization": f"Bearer {org.bison_organization_api_key}",
                    "Content-Type": "application/json"
                },
                params={
                    "page": current_page,
                    "per_page": per_page
                }
            )
            
            if response.status_code != 200:
                log_to_terminal("Signals", "SendingPower", f"API Error: {response.text}")
                break
                
            data = response.json()
            accounts = data.get('data', [])
            all_bison_accounts.extend(accounts)
            
            # Check if we've reached the last page
            total_pages = data.get('meta', {}).get('last_page', 1)
            if current_page >= total_pages:
                break
                
            current_page += 1
        
        log_to_terminal("Signals", "SendingPower", f"Found {len(all_bison_accounts)} accounts for {org.bison_organization_name}")
        
        # Deduplicate accounts by email to handle Bison API duplicate issue
        unique_emails = {}
        for account in all_bison_accounts:
            email = account.get('email')
            if email and email not in unique_emails:
                unique_emails[email] = account
        
        bison_emails = list(unique_emails.keys())
        
        if not bison_emails:
            log_to_terminal("Signals", "SendingPower", f"No valid email addresses found for {org.bison_organization_name}")
            return
        
        # 2. Get latest reports for these accounts
        with connection.cursor() as cursor:
            # Process emails in chunks to avoid too many SQL parameters
            chunk_size = 500
            email_chunks = [bison_emails[i:i + chunk_size] for i in range(0, len(bison_emails), chunk_size)]
            
            total_sending_power = 0
            
            for chunk in email_chunks:
                placeholders = ','.join(['%s'] * len(chunk))
                query = f"""
                WITH latest_reports AS (
                    SELECT 
                        ubr.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY ubr.email_account
                            ORDER BY ubr.created_at DESC
                        ) as rn
                    FROM user_spamcheck_bison_reports ubr
                    WHERE ubr.bison_organization_id = %s
                    AND ubr.email_account IN ({placeholders})
                )
                SELECT 
                    COUNT(CASE WHEN is_good = true THEN 1 END) * COALESCE(MAX(sending_limit), 25) as daily_power
                FROM latest_reports
                WHERE rn = 1
                """
                
                cursor.execute(query, [org.id] + chunk)
                result = cursor.fetchone()
                
                if result:
                    chunk_power = result[0] or 0
                    total_sending_power += chunk_power
            
            # Create a new sending power record
            UserBisonSendingPower.objects.create(
                user=user,
                bison_organization=org,
                report_date=report_date,
                sending_power=total_sending_power
            )
            
            log_to_terminal("Signals", "SendingPower", f"Created sending power record for {org.bison_organization_name}: {total_sending_power}")
    
    except Exception as e:
        log_to_terminal("Signals", "SendingPower", f"Error updating sending power: {str(e)}")
        import traceback
        log_to_terminal("Signals", "SendingPower", f"Traceback: {traceback.format_exc()}") 