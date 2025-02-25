import time
import requests
import json
from django.db import connection
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from spamcheck.models import UserSpamcheckBison
from settings.api import log_to_terminal
from .models import UserCampaignsBison

@receiver(post_save, sender=UserSpamcheckBison)
def handle_spamcheck_status_change(sender, instance, created, **kwargs):
    """
    Signal handler to detect when a Bison spamcheck status changes to "completed".
    When this happens, we update the UserCampaignsBison table with fresh data.
    """
    # Skip if this is a new record being created
    if created:
        return
        
    # Check if status is now "completed"
    if instance.status == "completed":
        log_to_terminal("BisonCampaigns", "Signal", f"Detected completed spamcheck: {instance.name} (ID: {instance.id})")
        # Call function to update campaigns table
        update_bison_campaigns_table(instance)

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
        
        api_url = f"{org.base_url.rstrip('/')}/api/campaigns"
        log_to_terminal("BisonCampaigns", "Debug", f"Fetching all campaigns from: {api_url}")
        
        # Use GET request instead of POST to avoid the "name field is required" error
        response = requests.get(
            api_url,
            headers={
                "Authorization": f"Bearer {org.bison_organization_api_key}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code != 200:
            log_to_terminal("BisonCampaigns", "Error", f"Error fetching all campaigns: {response.text}")
            # Continue with the campaigns we already have from step 2
            campaigns_details = {}
        else:
            all_campaigns_data = response.json().get('data', [])
            log_to_terminal("BisonCampaigns", "Debug", f"API returned {len(all_campaigns_data)} campaigns")
            
            # Debug: Print first campaign details
            if all_campaigns_data:
                first_campaign = all_campaigns_data[0]
                log_to_terminal("BisonCampaigns", "Debug", f"Sample campaign details: {json.dumps(first_campaign)}")
            
            campaigns_details = {campaign.get('id'): campaign for campaign in all_campaigns_data}
        
        details_time = time.time() - details_start_time
        log_to_terminal("BisonCampaigns", "Details", f"Fetched details for {len(campaigns_details)} campaigns (took {details_time:.2f}s)")
        
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
        
        # Update or create each campaign
        for campaign_id, data in account_campaigns.items():
            campaign = data['campaign']
            accounts = data['accounts']
            campaign_details = campaigns_details.get(campaign_id, {})
            
            log_to_terminal("BisonCampaigns", "Debug", f"Processing campaign: {campaign.get('name', '')} (ID: {campaign_id})")
            
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
            
            if has_accounts_with_scores:
                avg_google_score = sum(google_scores) / len(google_scores) if google_scores else 0
                avg_outlook_score = sum(outlook_scores) / len(outlook_scores) if outlook_scores else 0
                
                # Calculate average sending limit of connected accounts
                sending_limits = [account_scores.get(email, {}).get('sending_limit', 25) for email in campaign_account_emails if email in account_scores]
                avg_sending_limit = round(sum(sending_limits) / len(sending_limits)) if sending_limits else 25
                
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} average scores: Google={avg_google_score:.2f}, Outlook={avg_outlook_score:.2f}")
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} max daily sends: {campaign.get('max_emails_per_day', 0)}, avg sending limit: {avg_sending_limit}")
            else:
                # No accounts with scores found, set to null/None
                avg_google_score = 0
                avg_outlook_score = 0
                avg_sending_limit = None
                log_to_terminal("BisonCampaigns", "Debug", f"Campaign {campaign_id} has NO accounts with scores in our database. Setting sends_per_account to NULL")
            
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
                camp_obj.sends_per_account = avg_sending_limit
                camp_obj.google_score = round(avg_google_score, 2)
                camp_obj.outlook_score = round(avg_outlook_score, 2)
                camp_obj.max_daily_sends = max_daily_sends
                camp_obj.save()
                log_to_terminal("BisonCampaigns", "Update", f"Updated campaign: {camp_obj.campaign_name} (ID: {campaign_id})")
            else:
                # Create new record
                log_to_terminal("BisonCampaigns", "Debug", f"Creating new campaign {campaign_id} in database")
                UserCampaignsBison.objects.create(
                    user=user,
                    bison_organization=org,
                    campaign_id=campaign_id,
                    campaign_name=campaign.get('name', ''),
                    connected_emails_count=len(accounts),
                    sends_per_account=avg_sending_limit,
                    google_score=round(avg_google_score, 2),
                    outlook_score=round(avg_outlook_score, 2),
                    max_daily_sends=max_daily_sends
                )
                log_to_terminal("BisonCampaigns", "Create", f"Created campaign: {campaign.get('name', '')} (ID: {campaign_id})")
            
            processed_campaign_ids.add(campaign_id)
        
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