from typing import List, Optional, Dict
from ninja import Router, Schema
from ninja.pagination import paginate
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError, connection
from django.core.exceptions import ObjectDoesNotExist
from authentication.authorization import AuthBearer
from .schema import CreateSpamcheckSchema, UpdateSpamcheckSchema, LaunchSpamcheckSchema, ListAccountsRequestSchema, ListAccountsResponseSchema, ListSpamchecksResponseSchema, CreateSpamcheckBisonSchema, UpdateSpamcheckBisonSchema, SpamcheckBisonDetailsResponseSchema, BisonAccountsReportsResponseSchema, CampaignCopyResponse, BisonAccountDetailsResponseSchema
from .models import UserSpamcheck, UserSpamcheckAccounts, UserSpamcheckCampaignOptions, UserSpamcheckCampaigns, UserSpamcheckReport, UserSpamcheckBison, UserSpamcheckAccountsBison, UserSpamcheckBisonReport, SpamcheckErrorLog
from settings.models import UserInstantly, UserSettings, UserBison
import requests
from django.conf import settings
from datetime import datetime
from django.utils import timezone
import pytz
from ninja.errors import HttpError
from settings.api import log_to_terminal
import re
from html import unescape
from ninja import Field
import json

router = Router(tags=["spamcheck"])

def round_to_quarter(score: float) -> float:
    """Round a score to the nearest quarter (0.25, 0.5, 0.75, 1.0)
    If score is less than 0.125, return 0
    """
    if score is None:
        return 0.0
    
    # Convert to float and ensure it's between 0 and 1
    score = float(score)
    if score < 0.125:  # Less than halfway to 0.25
        return 0.0
    
    # Round to nearest quarter
    quarters = round(score * 4) / 4
    return min(1.0, max(0.0, quarters))

class LastCheckData(Schema):
    """Schema for last check data"""
    id: str
    date: str

class AccountHistoryData(Schema):
    """Schema for account history data"""
    total_checks: int
    good_checks: int
    bad_checks: int

class AccountData(Schema):
    """Schema for account data"""
    email: str
    domain: str
    sends_per_day: int
    google_score: float
    outlook_score: float
    status: str
    workspace: str
    last_check: LastCheckData
    reports_link: str  # Added reports link field
    history: AccountHistoryData  # Added history field
    bounce_count: Optional[int] = None  # Added bounce count field
    reply_count: Optional[int] = None  # Added reply count field
    emails_sent: Optional[int] = None  # Added total emails sent field

class PaginationMeta(Schema):
    """Schema for pagination metadata"""
    total: int
    page: int
    per_page: int
    total_pages: int

class AccountsResponse(Schema):
    """Schema for accounts endpoint response"""
    data: List[AccountData]
    meta: PaginationMeta

@router.post("/create-spamcheck-instantly", auth=AuthBearer())
def create_spamcheck_instantly(request, payload: CreateSpamcheckSchema):
    """
    Create a new spamcheck with accounts and options
    
    Parameters:
        - name: Name of the spamcheck
        - user_organization_id: ID of the Instantly organization to use
        - accounts: List of email accounts to check (e.g. ["test1@example.com", "test2@example.com"])
        - open_tracking: Whether to track email opens
        - link_tracking: Whether to track link clicks
        - text_only: Whether to send text-only emails
        - subject: Email subject template
        - body: Email body template
        - scheduled_at: When to run the spamcheck
        - recurring_days: Optional, number of days for recurring checks
        - reports_waiting_time: Optional, reports waiting time
    """
    user = request.auth
    
    # Validate accounts list is not empty
    if not payload.accounts:
        return {
            "success": False,
            "message": "At least one email account is required"
        }
    
    # Get the specific organization with better error handling
    try:
        user_organization = UserInstantly.objects.get(
            id=payload.user_organization_id,
            user=user
        )
        
        if not user_organization.instantly_organization_status:
            return {
                "success": False,
                "message": f"Organization with ID {payload.user_organization_id} exists but is not active. Please activate it first."
            }
            
    except ObjectDoesNotExist:
        return {
            "success": False,
            "message": f"Organization with ID {payload.user_organization_id} not found. Please check if the organization ID is correct and belongs to your account."
        }
    
    # Check if spamcheck with same name exists
    existing_spamcheck = UserSpamcheck.objects.filter(
        user=user,
        user_organization=user_organization,
        name=payload.name
    ).first()
    
    if existing_spamcheck:
        return {
            "success": False,
            "message": f"A spamcheck with the name '{payload.name}' already exists for this organization. Please use a different name."
        }
    
    try:
        with transaction.atomic():
            # Create spamcheck first
            spamcheck = UserSpamcheck.objects.create(
                user=user,
                user_organization=user_organization,
                name=payload.name,
                scheduled_at=payload.scheduled_at,
                recurring_days=payload.recurring_days,
                is_domain_based=payload.is_domain_based,
                conditions=payload.conditions,
                reports_waiting_time=payload.reports_waiting_time
            )
            
            # Create spamcheck options with spamcheck reference
            options = UserSpamcheckCampaignOptions.objects.create(
                spamcheck=spamcheck,
                open_tracking=payload.open_tracking,
                link_tracking=payload.link_tracking,
                text_only=payload.text_only,
                subject=payload.subject,
                body=payload.body
            )
            
            # Update spamcheck with options reference
            spamcheck.options = options
            spamcheck.save()
            
            # Create accounts
            accounts = []
            for email in payload.accounts:
                account = UserSpamcheckAccounts.objects.create(
                    user=user,
                    organization=user_organization,
                    spamcheck=spamcheck,
                    email_account=email
                )
                accounts.append(account)
            
            return {
                "success": True,
                "message": "Spamcheck created successfully",
                "data": {
                    "id": spamcheck.id,
                    "name": spamcheck.name,
                    "scheduled_at": spamcheck.scheduled_at,
                    "recurring_days": spamcheck.recurring_days,
                    "status": spamcheck.status,
                    "accounts_count": len(accounts),
                    "user_organization_id": user_organization.id,
                    "organization_name": user_organization.instantly_organization_name
                }
            }
            
    except IntegrityError:
        return {
            "success": False,
            "message": f"A spamcheck with the name '{payload.name}' already exists for this organization. Please use a different name."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error creating spamcheck: {str(e)}. Please try again or contact support if the issue persists."
        }

@router.put("/update-spamcheck-instantly/{spamcheck_id}", auth=AuthBearer())
def update_spamcheck_instantly(request, spamcheck_id: int, payload: UpdateSpamcheckSchema):
    """
    Update an existing spamcheck
    
    Parameters:
        - spamcheck_id: ID of the spamcheck to update
        - name: Optional, new name for the spamcheck
        - accounts: Optional, new list of email accounts
        - open_tracking: Optional, whether to track email opens
        - link_tracking: Optional, whether to track link clicks
        - text_only: Optional, whether to send text-only emails
        - subject: Optional, new email subject template
        - body: Optional, new email body template
        - scheduled_at: Optional, new scheduled time
        - recurring_days: Optional, new recurring days setting
    """
    user = request.auth
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = UserSpamcheck.objects.select_related('options').get(
            id=spamcheck_id,
            user=user
        )
        
        # Check if status allows updates
        if spamcheck.status not in ['pending', 'failed', 'completed']:
            return {
                "success": False,
                "message": f"Cannot update spamcheck with status '{spamcheck.status}'. Only pending, failed, or completed spamchecks can be updated."
            }
        
        try:
            with transaction.atomic():
                # Update spamcheck fields if provided
                if payload.name is not None:
                    spamcheck.name = payload.name
                if payload.scheduled_at is not None:
                    spamcheck.scheduled_at = payload.scheduled_at
                if payload.recurring_days is not None:
                    spamcheck.recurring_days = payload.recurring_days
                spamcheck.save()
                
                # Update options if any option field is provided
                options_updated = False
                if any(field is not None for field in [
                    payload.open_tracking, payload.link_tracking,
                    payload.text_only, payload.subject, payload.body
                ]):
                    options = spamcheck.options
                    if payload.open_tracking is not None:
                        options.open_tracking = payload.open_tracking
                    if payload.link_tracking is not None:
                        options.link_tracking = payload.link_tracking
                    if payload.text_only is not None:
                        options.text_only = payload.text_only
                    if payload.subject is not None:
                        options.subject = payload.subject
                    if payload.body is not None:
                        options.body = payload.body
                    options.save()
                    options_updated = True
                
                # Update accounts if provided
                accounts_updated = False
                if payload.accounts is not None:
                    # Delete existing accounts
                    UserSpamcheckAccounts.objects.filter(spamcheck=spamcheck).delete()
                    
                    # Create new accounts
                    for email in payload.accounts:
                        UserSpamcheckAccounts.objects.create(
                            user=user,
                            organization=spamcheck.user_organization,
                            spamcheck=spamcheck,
                            email_account=email
                        )
                    accounts_updated = True
                
                return {
                    "success": True,
                    "message": "Spamcheck updated successfully",
                    "data": {
                        "id": spamcheck.id,
                        "name": spamcheck.name,
                        "scheduled_at": spamcheck.scheduled_at,
                        "recurring_days": spamcheck.recurring_days,
                        "status": spamcheck.status,
                        "options_updated": options_updated,
                        "accounts_updated": accounts_updated
                    }
                }
                
        except IntegrityError:
            return {
                "success": False,
                "message": f"A spamcheck with the name '{payload.name}' already exists for this organization. Please use a different name."
            }
            
    except UserSpamcheck.DoesNotExist:
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to update it."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error updating spamcheck: {str(e)}. Please try again or contact support if the issue persists."
        }

@router.delete("/delete-spamcheck-instantly/{spamcheck_id}", auth=AuthBearer())
def delete_spamcheck_instantly(request, spamcheck_id: int):
    """
    Delete a spamcheck and all its related data
    
    Parameters:
        - spamcheck_id: ID of the spamcheck to delete
    """
    user = request.auth
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = UserSpamcheck.objects.get(
            id=spamcheck_id,
            user=user
        )
        
        # Check if status allows deletion
        if spamcheck.status in ['in_progress', 'generating_reports']:
            return {
                "success": False,
                "message": f"Cannot delete spamcheck with status '{spamcheck.status}'. Only spamchecks that are not in progress or generating reports can be deleted."
            }
        
        # Store name for response
        spamcheck_name = spamcheck.name
        
        # Delete the spamcheck (this will cascade delete options and accounts)
        spamcheck.delete()
        
        return {
            "success": True,
            "message": f"Spamcheck '{spamcheck_name}' and all related data deleted successfully"
        }
        
    except UserSpamcheck.DoesNotExist:
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to delete it."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error deleting spamcheck: {str(e)}. Please try again or contact support if the issue persists."
        }

@router.post("/launch-spamcheck-instantly", auth=AuthBearer())
def launch_spamcheck_instantly(request, payload: LaunchSpamcheckSchema):
    """
    Launch a spamcheck immediately
    """
    user = request.auth
    spamcheck = None
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = UserSpamcheck.objects.select_related(
            'options',
            'user_organization'
        ).get(
            id=payload.spamcheck_id,
            user=user
        )
        
        # Check if status allows launching
        if spamcheck.status not in ['pending', 'failed', 'completed']:
            return {
                "success": False,
                "message": f"Cannot launch spamcheck with status '{spamcheck.status}'. Only pending, failed, or completed spamchecks can be launched."
            }
            
        # Get user settings
        try:
            user_settings = UserSettings.objects.get(user=user)
        except UserSettings.DoesNotExist:
            error_message = "User settings not found. Please configure API keys first."
            # Log the error
            SpamcheckErrorLog.objects.create(
                user=user,
                spamcheck=spamcheck,
                error_type='validation_error',
                provider='system',
                error_message=error_message,
                step='initialization'
            )
            return {
                "success": False,
                "message": error_message
            }
            
        # Verify user settings and tokens
        if not user_settings.emailguard_api_key:
            error_message = "EmailGuard API key not found. Please configure it first."
            # Log the error
            SpamcheckErrorLog.objects.create(
                user=user,
                spamcheck=spamcheck,
                error_type='authentication_error',
                provider='emailguard',
                error_message=error_message,
                step='initialization'
            )
            return {
                "success": False,
                "message": error_message
            }
            
        if not user_settings.instantly_user_token or not user_settings.instantly_status:
            error_message = "Instantly user token not found or inactive. Please reconnect your Instantly account."
            # Log the error
            SpamcheckErrorLog.objects.create(
                user=user,
                spamcheck=spamcheck,
                error_type='authentication_error',
                provider='instantly',
                error_message=error_message,
                step='initialization'
            )
            return {
                "success": False,
                "message": error_message
            }
            
        # Verify organization status and tokens
        if not spamcheck.user_organization.instantly_organization_status:
            error_message = "Selected Instantly organization is not active. Please activate it first."
            # Log the error
            SpamcheckErrorLog.objects.create(
                user=user,
                spamcheck=spamcheck,
                error_type='authentication_error',
                provider='instantly',
                error_message=error_message,
                step='organization_validation'
            )
            return {
                "success": False,
                "message": error_message
            }
            
        if not spamcheck.user_organization.instantly_organization_token:
            error_message = "Organization token not found. Please reconnect the organization."
            # Log the error
            SpamcheckErrorLog.objects.create(
                user=user,
                spamcheck=spamcheck,
                error_type='authentication_error',
                provider='instantly',
                error_message=error_message,
                step='organization_validation'
            )
            return {
                "success": False,
                "message": error_message
            }
            
        # Start transaction and set status to in_progress at the beginning
        with transaction.atomic():
            # Update status to in_progress before starting
            spamcheck.status = 'in_progress'
            spamcheck.save()
            
            # Get accounts
            accounts = spamcheck.accounts.all()
            if not accounts:
                error_message = "No accounts found for this spamcheck."
                # Log the error
                SpamcheckErrorLog.objects.create(
                    user=user,
                    spamcheck=spamcheck,
                    error_type='validation_error',
                    provider='system',
                    error_message=error_message,
                    step='account_validation'
                )
                # Set status back to failed
                spamcheck.status = 'failed'
                spamcheck.save()
                return {
                    "success": False,
                    "message": error_message
                }
                
            # For each account
            for account in accounts:
                try:
                    print(f"\n{'='*50}")
                    print(f"Processing account: {account.email_account}")
                    print(f"{'='*50}")
                    
                    # 1. Update email account sending limit
                    print("\n[1/5] Updating email account sending limit...", flush=True)
                    print("Calling Instantly API endpoint: POST https://app.instantly.ai/backend/api/v1/account/update/bulk")
                    
                    update_limit_data = {
                        "payload": {
                            "daily_limit": "100"  # Set daily limit to 100
                        },
                        "emails": [account.email_account]
                    }
                    
                    update_limit_response = requests.post(
                        "https://app.instantly.ai/backend/api/v1/account/update/bulk",
                        headers={
                            "Cookie": f"__session={user_settings.instantly_user_token}",
                            "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                            "Content-Type": "application/json"
                        },
                        json=update_limit_data,
                        timeout=30
                    )
                    
                    if update_limit_response.status_code != 200:
                        raise Exception(f"Failed to update account limit: {update_limit_response.text}")
                    
                    print("✓ Account sending limit updated")

                    # 2. Get emailguard tag
                    print("\n[2/5] Getting EmailGuard tag...", flush=True)
                    campaign_name = f"{spamcheck.name} - {account.email_account}"
                    if payload.is_test:
                        campaign_name = f"[TEST] {campaign_name}"

                    print("Calling EmailGuard API endpoint: POST https://app.emailguard.io/api/v1/inbox-placement-tests")
                    emailguard_headers = {
                        "Authorization": f"Bearer {user_settings.emailguard_api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    emailguard_data = {
                        "name": campaign_name,
                        "type": "inbox_placement"
                    }
                    print(f"Request Headers: {emailguard_headers}")
                    print(f"Request Data: {emailguard_data}")
                    
                    emailguard_response = requests.post(
                        "https://app.emailguard.io/api/v1/inbox-placement-tests",
                        headers=emailguard_headers,
                        json=emailguard_data,
                        timeout=30
                    )
                    
                    if emailguard_response.status_code not in [200, 201]:  # Accept both 200 and 201 as success
                        raise Exception(f"Failed to get EmailGuard tag: {emailguard_response.text}")
                    
                    emailguard_data = emailguard_response.json()
                    if "data" not in emailguard_data or "uuid" not in emailguard_data["data"]:
                        raise Exception(f"EmailGuard response missing uuid: {emailguard_data}")
                    
                    emailguard_tag = emailguard_data["data"]["uuid"]
                    test_emails = emailguard_data["data"]["inbox_placement_test_emails"]
                    print(f"✓ Got EmailGuard tag (UUID): {emailguard_tag}")
                    print(f"✓ Got {len(test_emails)} test email addresses")

                    # 3. Create campaign with all settings
                    print("\n[3/5] Creating campaign with settings...", flush=True)
                    print("Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/campaigns")
                    
                    # Calculate schedule time based on campaign timezone
                    campaign_tz = pytz.timezone('Etc/GMT+12')
                    user_timezone = user.profile.timezone if hasattr(user, 'profile') else 'UTC'
                    user_tz = pytz.timezone(user_timezone)
                    
                    # Get current time in user timezone
                    current_time = timezone.localtime(timezone.now(), user_tz)
                    
                    # Convert to campaign timezone
                    current_time_campaign_tz = current_time.astimezone(campaign_tz)
                    
                    # Round to nearest 30 minutes
                    minutes = current_time_campaign_tz.minute
                    if minutes < 30:
                        start_minutes = "30"
                        start_hour = str(current_time_campaign_tz.hour).zfill(2)
                    else:
                        start_minutes = "00"
                        start_hour = str((current_time_campaign_tz.hour + 1) % 24).zfill(2)

                    # Calculate end hour (1 hour after start)
                    end_hour = str((int(start_hour) + 1) % 24).zfill(2)
                    
                    request_headers = {
                        "Authorization": f"Bearer {spamcheck.user_organization.instantly_api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    print(f"Scheduling campaign in Etc/GMT+12 timezone:")
                    print(f"Start time: {start_hour}:{start_minutes}")
                    print(f"End time: {end_hour}:{start_minutes}")
                    
                    request_data = {
                        "name": campaign_name,
                        "campaign_schedule": {
                            "schedules": [
                                {
                                    "name": "Default Schedule",
                                    "timing": {
                                        "from": f"{start_hour}:{start_minutes}",
                                        "to": f"{end_hour}:{start_minutes}"
                                    },
                                    "days": {
                                        "0": True,  # Sunday
                                        "1": True,  # Monday
                                        "2": True,  # Tuesday
                                        "3": True,  # Wednesday
                                        "4": True,  # Thursday
                                        "5": True,  # Friday
                                        "6": True   # Saturday
                                    },
                                    "timezone": "Etc/GMT+12"
                                }
                            ]
                        },
                        "email_gap": 1,
                        "text_only": spamcheck.options.text_only,
                        "email_list": [account.email_account],
                        "daily_limit": 100,
                        "stop_on_reply": True,
                        "stop_on_auto_reply": True,
                        "link_tracking": spamcheck.options.link_tracking,
                        "open_tracking": spamcheck.options.open_tracking,
                        "sequences": [{
                            "steps": [{
                                "type": "email",
                                "variants": [{
                                    "subject": spamcheck.options.subject,
                                    "body": f"{spamcheck.options.body}\n\n{emailguard_tag}"
                                }]
                            }]
                        }]
                    }
                    
                    print(f"Request Headers: {request_headers}")
                    print(f"Request Data: {request_data}")
                        
                    campaign_response = requests.post(
                        "https://api.instantly.ai/api/v2/campaigns",
                        headers=request_headers,
                        json=request_data,
                        timeout=30
                    )
                    
                    if campaign_response.status_code != 200:
                        raise Exception(f"Failed to create campaign: {campaign_response.text}")
                    
                    campaign_data = campaign_response.json()
                    if not campaign_data or not isinstance(campaign_data, dict):
                        raise Exception(f"Invalid response from campaign creation: {campaign_data}")
                    
                    if 'id' not in campaign_data:
                        raise Exception(f"Campaign ID not found in response: {campaign_data}")
                    
                    campaign_id = campaign_data["id"]
                    print(f"✓ Campaign created with ID: {campaign_id}")
                    
                    # 4. Add leads
                    print("\n[4/5] Adding leads...", flush=True)
                    print("Calling Instantly API endpoint: POST https://app.instantly.ai/backend/api/v1/lead/add")
                    
                    leads_data = {
                        "campaign_id": campaign_id,
                        "skip_if_in_workspace": False,
                        "skip_if_in_campaign": False,
                        "leads": [{"email": email["email"]} for email in test_emails]
                    }
                    
                    print(f"Adding {len(test_emails)} leads in bulk")
                    
                    leads_response = requests.post(
                        "https://app.instantly.ai/backend/api/v1/lead/add",
                        headers={
                            "Cookie": f"__session={user_settings.instantly_user_token}",
                            "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                            "Content-Type": "application/json"
                        },
                        json=leads_data,
                        timeout=30
                    )
                    
                    if leads_response.status_code != 200:
                        raise Exception(f"Failed to add leads: {leads_response.text}")
                    
                    print(f"✓ Successfully added {len(test_emails)} leads in bulk")
                    
                    # 5. Launch campaign
                    print("\n[5/5] Launching campaign...", flush=True)
                    print(f"Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate")
                    
                    launch_response = requests.post(
                        f"https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate",
                        headers=request_headers,  # Reuse the Bearer token headers
                        json={},  # Empty body required
                        timeout=30
                    )
                    
                    if launch_response.status_code != 200:
                        raise Exception(f"Failed to launch campaign: {launch_response.text}")
                    
                    print("✓ Campaign launched successfully")
                    
                    # Store campaign info after successful launch
                    print("\nStoring campaign info in database...", flush=True)
                    UserSpamcheckCampaigns.objects.create(
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
                    
                except Exception as e:
                    error_message = f"Error processing account {account.email_account}: {str(e)}"
                    print(error_message)
                    
                    # Determine error type and provider based on the exception message
                    error_type = 'unknown_error'
                    provider = 'system'
                    step = 'unknown'
                    api_endpoint = None
                    status_code = None
                    
                    if "Failed to update account limit" in str(e):
                        error_type = 'api_error'
                        provider = 'instantly'
                        step = 'update_account_limit'
                        api_endpoint = "https://app.instantly.ai/backend/api/v1/account/update/bulk"
                    elif "Failed to get EmailGuard tag" in str(e):
                        error_type = 'api_error'
                        provider = 'emailguard'
                        step = 'get_emailguard_tag'
                        api_endpoint = "https://app.emailguard.io/api/v1/inbox-placement-tests"
                    elif "Failed to create campaign" in str(e):
                        error_type = 'api_error'
                        provider = 'instantly'
                        step = 'create_campaign'
                        api_endpoint = "https://api.instantly.ai/api/v2/campaigns"
                    elif "Failed to add leads" in str(e):
                        error_type = 'api_error'
                        provider = 'instantly'
                        step = 'add_leads'
                        api_endpoint = "https://app.instantly.ai/backend/api/v1/lead/add"
                    elif "Failed to launch campaign" in str(e):
                        error_type = 'api_error'
                        provider = 'instantly'
                        step = 'launch_campaign'
                        api_endpoint = "https://api.instantly.ai/api/v2/campaigns/{campaign_id}/activate"
                    elif "timeout" in str(e).lower():
                        error_type = 'timeout_error'
                    elif "connection" in str(e).lower():
                        error_type = 'connection_error'
                    
                    # Extract status code if present in the error message
                    import re
                    status_code_match = re.search(r'status code (\d+)', str(e))
                    if status_code_match:
                        try:
                            status_code = int(status_code_match.group(1))
                        except:
                            pass
                    
                    # Log the error
                    SpamcheckErrorLog.objects.create(
                        user=user,
                        spamcheck=spamcheck,
                        error_type=error_type,
                        provider=provider,
                        error_message=str(e),
                        error_details={
                            'full_error': str(e),
                            'account_email': account.email_account
                        },
                        account_email=account.email_account,
                        step=step,
                        api_endpoint=api_endpoint,
                        status_code=status_code
                    )
                    
                    # Update spamcheck status to failed
                    spamcheck.status = 'failed'
                    spamcheck.save()
                    
                    return {
                        "success": False,
                        "message": error_message
                    }
            
            return {
                "success": True,
                "message": "Spamcheck launched successfully",
                "data": {
                    "id": spamcheck.id,
                    "name": spamcheck.name,
                    "status": spamcheck.status,
                    "campaigns_count": len(accounts)
                }
            }
            
    except UserSpamcheck.DoesNotExist:
        error_message = f"Spamcheck with ID {payload.spamcheck_id} not found or you don't have permission to launch it."
        # Log the error
        SpamcheckErrorLog.objects.create(
            user=user,
            error_type='validation_error',
            provider='system',
            error_message=error_message,
            step='spamcheck_lookup'
        )
        return {
            "success": False,
            "message": error_message
        }
    except Exception as e:
        error_message = f"Error launching spamcheck: {str(e)}. Please try again or contact support if the issue persists."
        
        # Log the error
        SpamcheckErrorLog.objects.create(
            user=user,
            spamcheck=spamcheck if spamcheck else None,
            error_type='unknown_error',
            provider='system',
            error_message=str(e),
            error_details={'full_error': str(e)},
            step='general'
        )
        
        # If we have a spamcheck object, update its status to failed
        if spamcheck:
            spamcheck.status = 'failed'
            spamcheck.save()
            
        return {
            "success": False,
            "message": error_message
        }

@router.post("/clear-organization-spamchecks/{organization_id}", auth=AuthBearer())
def clear_organization_spamchecks(request, organization_id: int):
    """
    Clear all spamchecks for a specific organization
    
    Parameters:
        - organization_id: ID of the organization to clear spamchecks for
    """
    user = request.auth
    
    try:
        # Get the organization and verify ownership
        organization = UserInstantly.objects.get(
            id=organization_id,
            user=user
        )
        
        # Get all spamchecks for this organization
        spamchecks = UserSpamcheck.objects.filter(
            user=user,
            user_organization=organization
        )
        
        if not spamchecks.exists():
            return {
                "success": False,
                "message": f"No spamchecks found for organization {organization.instantly_organization_name}"
            }
        
        with transaction.atomic():
            # First update all spamchecks to failed status
            spamchecks.update(status='failed')
            
            # Get all spamcheck IDs
            spamcheck_ids = list(spamchecks.values_list('id', flat=True))
            
            # Update reports to remove spamcheck reference
            UserSpamcheckReport.objects.filter(
                spamcheck_instantly__in=spamcheck_ids
            ).update(spamcheck_instantly=None)
            
            # Delete all spamchecks
            spamchecks.delete()
            
            return {
                "success": True,
                "message": f"Successfully cleared {len(spamcheck_ids)} spamchecks for organization {organization.instantly_organization_name}",
                "data": {
                    "organization_id": organization_id,
                    "organization_name": organization.instantly_organization_name,
                    "spamchecks_cleared": len(spamcheck_ids)
                }
            }
            
    except UserInstantly.DoesNotExist:
        return {
            "success": False,
            "message": f"Organization with ID {organization_id} not found or you don't have permission to access it."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error clearing spamchecks: {str(e)}. Please try again or contact support if the issue persists."
        }

@router.post("/toggle-pause/{spamcheck_id}", auth=AuthBearer())
def toggle_pause_spamcheck(request, spamcheck_id: int):
    """
    Toggle spamcheck between paused and pending status.
    Only works if current status is paused, pending, or completed.
    """
    user = request.auth
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = get_object_or_404(UserSpamcheck, id=spamcheck_id, user=user)
        
        # Check if status allows toggling
        if spamcheck.status not in ['pending', 'paused', 'completed']:
            return {
                "success": False,
                "message": f"Cannot toggle pause for spamcheck with status '{spamcheck.status}'. Only pending, paused, or completed spamchecks can be toggled."
            }
        
        # Toggle status
        new_status = 'paused' if spamcheck.status in ['pending', 'completed'] else 'pending'
        spamcheck.status = new_status
        spamcheck.save()
        
        return {
            "success": True,
            "message": f"Spamcheck '{spamcheck.name}' is now {new_status}",
            "data": {
                "id": spamcheck.id,
                "name": spamcheck.name,
                "status": new_status
            }
        }
        
    except UserSpamcheck.DoesNotExist:
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to modify it."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error toggling spamcheck pause status: {str(e)}"
        }

@router.post("/list-accounts", auth=AuthBearer(), response=ListAccountsResponseSchema)
def list_accounts(
    request,
    organization_id: int,
    payload: ListAccountsRequestSchema
):
    """
    List accounts from specified platform (Instantly or Bison) with filtering options
    """
    user = request.auth
    print("\n=== DEBUG: List Accounts Start ===")
    print(f"Parameters received:")
    print(f"- organization_id: {organization_id}")
    print(f"- platform: {payload.platform}")
    print(f"- search: {payload.search}")
    print(f"- ignore_tags: {payload.ignore_tags}")
    print(f"- only_tags: {payload.only_tags}")
    print(f"- is_active: {payload.is_active}")
    print(f"- limit: {payload.limit}")

    # Default empty response
    empty_response = {
        "success": False,
        "message": "",
        "data": {
            "organization_id": organization_id,
            "organization_name": "",
            "total_accounts": 0,
            "accounts": []
        }
    }
    
    try:
        # Get user settings first
        user_settings = UserSettings.objects.get(user=user)

        if payload.platform.lower() == "instantly":
            # Get the organization and verify ownership for Instantly
            try:
                organization = UserInstantly.objects.get(id=organization_id, user=user)
                if not organization.instantly_organization_status:
                    empty_response["message"] = f"Organization with ID {organization_id} exists but is not active. Please activate it first."
                    return empty_response
            except UserInstantly.DoesNotExist:
                empty_response["message"] = f"Instantly organization with ID {organization_id} not found. Please check if the organization ID is correct and belongs to your account."
                return empty_response

            empty_response["data"]["organization_name"] = organization.instantly_organization_name

            # Prepare request data for Instantly API
            request_data = {
                "limit": payload.limit,
                "include_tags": True  # We need tags for filtering
            }
            
            if payload.search:
                request_data["search"] = payload.search
                
            if payload.is_active:
                request_data["filter"] = {"status": 1}  # 1 for active accounts in Instantly
            
            # Get accounts from Instantly API
            print("\nStep 2: Fetching accounts from Instantly API...")
            print(f"Request data: {request_data}")
            
            response = requests.post(
                "https://app.instantly.ai/backend/api/v1/account/list",
                headers={
                    "Cookie": f"__session={user_settings.instantly_user_token}",
                    "X-Org-Auth": organization.instantly_organization_token,
                    "Content-Type": "application/json"
                },
                json=request_data,
                timeout=30
            )
            
            if response.status_code != 200:
                print(f"✗ API Error: {response.text}")
                empty_response["message"] = "Failed to fetch accounts from Instantly API"
                return empty_response
                
            response_data = response.json()
            accounts = response_data.get("accounts", [])
            
            # For Instantly platform
            # Extract just the emails and apply filters
            email_list = []
            for account in accounts:
                email = account.get("email")
                if not email:
                    continue
                    
                # Skip if account is not active (status != 1)
                if payload.is_active and account.get("status") != 1:
                    continue
                    
                # Get account tags
                account_tags = [tag.get("label", "").lower() for tag in account.get("tags", [])] if account.get("tags") else []
                
                # Skip if has any ignored tag
                if payload.ignore_tags and account_tags:
                    should_skip = False
                    for ignore_tag in payload.ignore_tags:
                        ignore_tag_lower = ignore_tag.lower()
                        if any(ignore_tag_lower == tag for tag in account_tags):
                            should_skip = True
                            break
                    if should_skip:
                        continue
                
                # Skip if doesn't have any of the required tags
                if payload.only_tags and account_tags:
                    has_required_tag = False
                    for only_tag in payload.only_tags:
                        only_tag_lower = only_tag.lower()
                        if any(only_tag_lower == tag for tag in account_tags):
                            has_required_tag = True
                            break
                    if not has_required_tag:
                        continue
                        
                email_list.append(email)

        else:  # Bison platform
            # Get user's Bison organization
            try:
                user_bison = UserBison.objects.get(
                    id=organization_id,
                    user_id=user.id,
                    bison_organization_status=True  # Make sure organization is active
                )
                empty_response["data"]["organization_name"] = user_bison.bison_organization_name
            except UserBison.DoesNotExist:
                empty_response["message"] = "Bison organization not found or is not active. Please check if the organization ID is correct and belongs to your account."
                return empty_response

            # Get accounts from Bison API
            print("\nStep 2: Fetching accounts from Bison API...")
            print(f"Using Bison organization: {user_bison.bison_organization_name}")
            
            # Initialize variables for pagination
            all_accounts = []
            page = 1
            has_more = True
            
            while has_more:
                params = {
                    "page": page
                }
                if payload.search:
                    params["search"] = payload.search
                
                # Use base URL from Bison organization
                api_url = f"{user_bison.base_url.rstrip('/')}/api/sender-emails"
                print(f"Calling Bison API: {api_url} (Page {page})")
                
                response = requests.get(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {user_bison.bison_organization_api_key}",
                        "Content-Type": "application/json"
                    },
                    params=params,
                    timeout=30
                )
                
                if response.status_code != 200:
                    print(f"✗ API Error: {response.text}")
                    empty_response["message"] = "Failed to fetch accounts from Bison API"
                    return empty_response
                
                response_data = response.json()
                current_page_accounts = response_data.get("data", [])
                
                # Check if we have more pages
                has_more = (
                    response_data and 
                    response_data.get("data") and 
                    isinstance(response_data.get("data"), list) and 
                    len(response_data.get("data")) > 0
                )
                
                if current_page_accounts:
                    all_accounts.extend(current_page_accounts)
                    print(f"✓ Found {len(current_page_accounts)} accounts on page {page}")
                    page += 1
                else:
                    print(f"No more accounts found on page {page}")
                    break
            
            print(f"Total accounts fetched: {len(all_accounts)}")
            
            # For Bison platform
            # Extract just the emails and apply filters
            email_list = []
            for account in all_accounts:
                email = account.get("email")
                if not email:
                    continue
                
                # Skip if account is not active
                if payload.is_active and account.get("status") != "Connected":
                    continue
                
                # Get account tags
                account_tags = [tag.get("name", "").lower() for tag in account.get("tags", [])] if account.get("tags") else []
                
                # Skip if has any ignored tag
                if payload.ignore_tags and account_tags:
                    should_skip = False
                    for ignore_tag in payload.ignore_tags:
                        ignore_tag_lower = ignore_tag.lower()
                        if any(ignore_tag_lower == tag for tag in account_tags):
                            should_skip = True
                            break
                    if should_skip:
                        continue
                
                # Skip if doesn't have any of the required tags
                if payload.only_tags and account_tags:
                    has_required_tag = False
                    for only_tag in payload.only_tags:
                        only_tag_lower = only_tag.lower()
                        if any(only_tag_lower == tag for tag in account_tags):
                            has_required_tag = True
                            break
                    if not has_required_tag:
                        continue
                
                email_list.append(email)
            
            # Apply limit after filtering all accounts
            if payload.limit:
                email_list = email_list[:payload.limit]
            
            print(f"Final filtered email count: {len(email_list)}")
        
        # Use the correct organization name based on platform
        org_name = organization.instantly_organization_name if payload.platform.lower() == "instantly" else user_bison.bison_organization_name
        
        return {
            "success": True,
            "message": "Accounts retrieved successfully",
            "data": {
                "organization_id": organization_id,
                "organization_name": org_name,
                "total_accounts": len(email_list),
                "accounts": email_list
            }
        }
        
    except UserInstantly.DoesNotExist:
        print("✗ Error: Organization not found")
        empty_response["message"] = f"Organization with ID {organization_id} not found or you don't have permission to access it."
        return empty_response
        
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        empty_response["message"] = f"Error listing accounts: {str(e)}. Please try again or contact support if the issue persists."
        return empty_response

@router.get("/list-spamchecks", auth=AuthBearer(), response=ListSpamchecksResponseSchema)
def list_spamchecks(
    request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    platform: Optional[str] = None,
    page: int = 1,
    per_page: int = 10
):
    """
    Get all spamchecks (both Instantly and Bison) with their details
    
    Parameters:
        - search: Optional search term to filter spamchecks by name
        - status: Optional status filter (queued, pending, in_progress, generating_reports, completed, failed, paused)
        - platform: Optional platform filter (instantly, bison)
        - page: Page number (default: 1)
        - per_page: Items per page (default: 10)
    """
    user = request.auth
    
    try:
        # Get all Instantly spamchecks for the user with related data
        instantly_spamchecks = UserSpamcheck.objects.select_related(
            'user_organization',
            'options'
        ).prefetch_related(
            'accounts',
            'campaigns'
        ).filter(user=user)
        
        # Apply search filter if provided
        if search:
            instantly_spamchecks = instantly_spamchecks.filter(name__icontains=search)
            
        # Apply status filter if provided
        if status:
            instantly_spamchecks = instantly_spamchecks.filter(status=status)
            
        # Apply platform filter if provided
        if platform and platform.lower() == 'bison':
            instantly_spamchecks = UserSpamcheck.objects.none()  # Empty queryset
        
        # Get all Bison spamchecks for the user with related data
        bison_spamchecks = UserSpamcheckBison.objects.select_related(
            'user_organization'
        ).prefetch_related(
            'accounts'
        ).filter(user=user)
        
        # Apply search filter if provided
        if search:
            bison_spamchecks = bison_spamchecks.filter(name__icontains=search)
            
        # Apply status filter if provided
        if status:
            bison_spamchecks = bison_spamchecks.filter(status=status)
            
        # Apply platform filter if provided
        if platform and platform.lower() == 'instantly':
            bison_spamchecks = UserSpamcheckBison.objects.none()  # Empty queryset
        
        spamcheck_list = []
        
        # Process Instantly spamchecks
        for spamcheck in instantly_spamchecks:
            # Get options as dict
            options_dict = {
                'open_tracking': spamcheck.options.open_tracking,
                'link_tracking': spamcheck.options.link_tracking,
                'text_only': spamcheck.options.text_only,
                'subject': spamcheck.options.subject,
                'body': spamcheck.options.body
            } if spamcheck.options else {}
            
            # Create spamcheck details
            spamcheck_details = {
                'id': spamcheck.id,
                'name': spamcheck.name,
                'status': spamcheck.status,
                'scheduled_at': spamcheck.scheduled_at,
                'recurring_days': spamcheck.recurring_days,
                'is_domain_based': spamcheck.is_domain_based,
                'conditions': spamcheck.conditions,
                'reports_waiting_time': spamcheck.reports_waiting_time,
                'created_at': spamcheck.created_at,
                'updated_at': spamcheck.updated_at,
                'user_organization_id': spamcheck.user_organization.id,
                'organization_name': spamcheck.user_organization.instantly_organization_name,
                'accounts_count': spamcheck.accounts.count(),
                'campaigns_count': spamcheck.campaigns.count(),
                'options': options_dict,
                'platform': 'instantly'  # Explicitly mark as Instantly platform
            }
            spamcheck_list.append(spamcheck_details)
        
        # Process Bison spamchecks
        for spamcheck in bison_spamchecks:
            # Create spamcheck details
            spamcheck_details = {
                'id': spamcheck.id,
                'name': spamcheck.name,
                'status': spamcheck.status,
                'scheduled_at': spamcheck.scheduled_at,
                'recurring_days': spamcheck.recurring_days,
                'is_domain_based': spamcheck.is_domain_based,
                'conditions': spamcheck.conditions,
                'reports_waiting_time': spamcheck.reports_waiting_time,
                'created_at': spamcheck.created_at,
                'updated_at': spamcheck.updated_at,
                'user_organization_id': spamcheck.user_organization.id,
                'organization_name': spamcheck.user_organization.bison_organization_name,
                'accounts_count': spamcheck.accounts.count(),
                'campaigns_count': 0,  # Bison doesn't use campaigns
                'options': {
                    'open_tracking': None,  # Bison doesn't support these
                    'link_tracking': None,  # Bison doesn't support these
                    'text_only': spamcheck.plain_text,
                    'subject': spamcheck.subject,
                    'body': spamcheck.body
                },
                'platform': 'bison'  # Explicitly mark as Bison platform
            }
            spamcheck_list.append(spamcheck_details)
        
        # Sort combined list by creation date (newest first)
        spamcheck_list.sort(key=lambda x: x['created_at'], reverse=True)
        
        # Calculate pagination
        total_count = len(spamcheck_list)
        total_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
        
        # Apply pagination
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        paginated_list = spamcheck_list[start_idx:end_idx]
        
        return {
            'success': True,
            'message': f'Successfully retrieved {total_count} spamchecks',
            'data': paginated_list,
            'meta': {
                'total': total_count,
                'page': page,
                'per_page': per_page,
                'total_pages': total_pages
            }
        }
        
    except Exception as e:
        log_to_terminal("Spamcheck", "List", f"Error retrieving spamchecks: {str(e)}")
        return {
            'success': False,
            'message': f'Error retrieving spamchecks: {str(e)}',
            'data': [],
            'meta': {
                'total': 0,
                'page': page,
                'per_page': per_page,
                'total_pages': 0
            }
        }

@router.get(
    "/accounts",
    response=AccountsResponse,
    auth=AuthBearer(),
    summary="Get Accounts List",
    description="Get a paginated list of email accounts with their latest check results"
)
def get_accounts(
    request,
    search: Optional[str] = None,
    status: Optional[str] = None,
    workspace: Optional[str] = None,
    filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 25
):
    """
    Get a list of email accounts with their latest check results.
    
    Args:
        search: Optional email search term
        status: Optional status filter (all, Inboxing, Resting)
        workspace: Optional workspace filter
        filter: Optional special filter (at-risk, protected)
        page: Page number (default: 1)
        per_page: Items per page (default: 25)
    """
    user = request.auth
    offset = (page - 1) * per_page
    
    # Build the base query
    query = """
        WITH latest_checks AS (
            SELECT 
                usr.*,
                ui.instantly_organization_name,
                ROW_NUMBER() OVER (
                    PARTITION BY usr.email_account 
                    ORDER BY usr.created_at DESC
                ) as rn
            FROM user_spamcheck_reports usr
            JOIN user_instantly ui ON usr.organization_id = ui.id
            WHERE ui.user_id = %s
    """
    params = [user.id]
    
    # Add search condition if provided
    if search:
        query += " AND usr.email_account LIKE %s"
        params.append(f"%{search}%")
    
    # Add workspace filter if provided
    if workspace:
        query += " AND ui.instantly_organization_name = %s"
        params.append(workspace)
    
    # Close the CTE and select from it
    query += """
        )
        SELECT 
            email_account,
            SUBSTRING_INDEX(email_account, '@', -1) as domain,
            COALESCE(sending_limit, 25) as sends_per_day,
            google_pro_score as google_score,
            outlook_pro_score as outlook_score,
            CASE 
                WHEN is_good THEN 'Inboxing'
                ELSE 'Resting'
            END as status,
            instantly_organization_name as workspace,
            id as check_id,
            created_at as check_date,
            report_link as reports_link,
            (
                SELECT COUNT(*) 
                FROM user_spamcheck_reports 
                WHERE email_account = lc.email_account
            ) as total_checks,
            (
                SELECT COUNT(*) 
                FROM user_spamcheck_reports 
                WHERE email_account = lc.email_account AND is_good = TRUE
            ) as good_checks,
            (
                SELECT COUNT(*) 
                FROM user_spamcheck_reports 
                WHERE email_account = lc.email_account AND is_good = FALSE
            ) as bad_checks,
            (
                SELECT COALESCE(MAX(bounced_count), 0)
                FROM user_spamcheck_bison_reports
                WHERE email_account = lc.email_account
            ) as bounce_count,
            (
                SELECT COALESCE(MAX(unique_replied_count), 0)
                FROM user_spamcheck_bison_reports
                WHERE email_account = lc.email_account
            ) as reply_count,
            (
                SELECT COALESCE(MAX(emails_sent_count), 0)
                FROM user_spamcheck_bison_reports
                WHERE email_account = lc.email_account
            ) as emails_sent,
            COUNT(*) OVER() as total_count
        FROM latest_checks lc
        WHERE rn = 1
    """
    
    # Add status filter
    if status and status.lower() != 'all':
        is_good = "TRUE" if status.lower() == 'inboxing' else "FALSE"
        query += f" AND is_good = {is_good}"
    
    # Add special filters
    if filter:
        if filter.lower() == 'at-risk':
            query += " AND is_good = FALSE"
        elif filter.lower() == 'protected':
            query += " AND is_good = TRUE"
    
    # Add pagination
    query += " ORDER BY check_date DESC LIMIT %s OFFSET %s"
    params.extend([per_page, offset])
    
    # Execute query
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        if not rows:
            return {
                "data": [],
                "meta": {
                    "total": 0,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": 0
                }
            }
        
        # Get total count from first row
        total_count = rows[0][-1]
        total_pages = (total_count + per_page - 1) // per_page
        
        # Format the data
        data = []
        for row in rows:
            (email, domain, sends_per_day, google_score, outlook_score, 
             status, workspace_name, check_id, check_date, reports_link, 
             total_checks, good_checks, bad_checks, bounce_count, reply_count, 
             emails_sent, _) = row
            
            data.append({
                "email": email,
                "domain": domain,
                "sends_per_day": sends_per_day,
                "google_score": round_to_quarter(google_score),
                "outlook_score": round_to_quarter(outlook_score),
                "status": status,
                "workspace": workspace_name,
                "last_check": {
                    "id": str(check_id),
                    "date": check_date.isoformat() if check_date else None
                },
                "reports_link": reports_link,
                "history": {
                    "total_checks": total_checks,
                    "good_checks": good_checks,
                    "bad_checks": bad_checks
                },
                "bounce_count": bounce_count,
                "reply_count": reply_count,
                "emails_sent": emails_sent
            })
        
        return {
            "data": data,
            "meta": {
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
        }

@router.get(
    "/accounts-bison",
    response=AccountsResponse,
    auth=AuthBearer(),
    summary="Get Bison Accounts List",
    description="Get a paginated list of Bison email accounts with their latest check results"
)
def get_bison_accounts(
    request,
    spamcheck_id: Optional[int] = None,
    search: Optional[str] = None,
    status: Optional[str] = None,
    workspace: Optional[str] = None,
    filter: Optional[str] = None,
    page: int = 1,
    per_page: int = 25
):
    """
    Get a list of Bison email accounts with their latest check results.
    
    Args:
        spamcheck_id: Optional ID of the spamcheck to filter accounts by
        search: Optional email search term
        status: Optional status filter (all, Inboxing, Resting)
        workspace: Optional workspace filter
        filter: Optional special filter (at-risk, protected)
        page: Page number (default: 1)
        per_page: Items per page (default: 25)
    """
    user = request.auth
    offset = (page - 1) * per_page
    
    # Build the base query
    query = """
        WITH latest_checks AS (
            SELECT 
                usbr.*,
                ub.bison_organization_name,
                usb.update_sending_limit,
                ROW_NUMBER() OVER (
                    PARTITION BY usbr.email_account 
                    ORDER BY usbr.created_at DESC
                ) as rn
            FROM user_spamcheck_bison_reports usbr
            JOIN user_bison ub ON usbr.bison_organization_id = ub.id
            JOIN user_spamcheck_bison usb ON usbr.spamcheck_bison_id = usb.id
            WHERE ub.user_id = %s
    """
    params = [user.id]
    
    # Add spamcheck filter if provided
    if spamcheck_id:
        query += " AND usbr.spamcheck_bison_id = %s"
        params.append(spamcheck_id)
    else:
        # Only apply update_sending_limit filter when no specific spamcheck_id is provided
        query += " AND usb.update_sending_limit = TRUE"
    
    # Add search condition if provided
    if search:
        query += " AND usbr.email_account LIKE %s"
        params.append(f"%{search}%")
    
    # Add workspace filter if provided
    if workspace:
        query += " AND ub.bison_organization_name = %s"
        params.append(workspace)
    
    # Close the CTE and select from it
    query += """
        ),
        account_stats AS (
            SELECT 
                usbr.email_account,
                COUNT(*) as total_checks,
                SUM(CASE WHEN usbr.is_good = TRUE THEN 1 ELSE 0 END) as good_checks,
                SUM(CASE WHEN usbr.is_good = FALSE THEN 1 ELSE 0 END) as bad_checks
            FROM user_spamcheck_bison_reports usbr
            JOIN user_spamcheck_bison usb ON usbr.spamcheck_bison_id = usb.id
            WHERE 1=1
    """
    
    # Add user filter to account_stats using the correct column name
    query += " AND usbr.bison_organization_id IN (SELECT id FROM user_bison WHERE user_id = %s)"
    params.append(user.id)
    
    # Add spamcheck filter to account_stats if provided
    if spamcheck_id:
        query += " AND usbr.spamcheck_bison_id = %s"
        params.append(spamcheck_id)
    else:
        # Only apply update_sending_limit filter when no specific spamcheck_id is provided
        query += " AND usb.update_sending_limit = TRUE"
    
    query += """
        GROUP BY usbr.email_account
        )
        SELECT 
            lc.email_account,
            SUBSTRING_INDEX(lc.email_account, '@', -1) as domain,
            COALESCE(lc.sending_limit, 25) as sends_per_day,
            lc.google_pro_score as google_score,
            lc.outlook_pro_score as outlook_score,
            CASE 
                WHEN lc.is_good THEN 'Inboxing'
                ELSE 'Resting'
            END as status,
            lc.bison_organization_name as workspace,
            lc.id as check_id,
            lc.created_at as check_date,
            lc.report_link as reports_link,
            COALESCE(ast.total_checks, 0) as total_checks,
            COALESCE(ast.good_checks, 0) as good_checks,
            COALESCE(ast.bad_checks, 0) as bad_checks,
            COALESCE(lc.bounced_count, 0) as bounce_count,
            COALESCE(lc.unique_replied_count, 0) as reply_count,
            COALESCE(lc.emails_sent_count, 0) as emails_sent,
            COUNT(*) OVER() as total_count
        FROM latest_checks lc
        LEFT JOIN account_stats ast ON lc.email_account = ast.email_account
        WHERE lc.rn = 1
    """
    
    # Add status filter
    if status and status.lower() != 'all':
        is_good = "TRUE" if status.lower() == 'inboxing' else "FALSE"
        query += f" AND lc.is_good = {is_good}"
    
    # Add special filters
    if filter:
        if filter.lower() == 'at-risk':
            query += " AND lc.is_good = FALSE"
        elif filter.lower() == 'protected':
            query += " AND lc.is_good = TRUE"
    
    # Add pagination
    query += " ORDER BY lc.created_at DESC LIMIT %s OFFSET %s"
    params.extend([per_page, offset])
    
    # Execute query
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        if not rows:
            return {
                "data": [],
                "meta": {
                    "total": 0,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": 0
                }
            }
        
        # Get total count from first row
        total_count = rows[0][-1]
        total_pages = (total_count + per_page - 1) // per_page
        
        # Format the data
        data = []
        for row in rows:
            (email, domain, sends_per_day, google_score, outlook_score, 
             status, workspace_name, check_id, check_date, reports_link, 
             total_checks, good_checks, bad_checks, bounce_count, reply_count, 
             emails_sent, _) = row
            
            data.append({
                "email": email,
                "domain": domain,
                "sends_per_day": sends_per_day,
                "google_score": round_to_quarter(google_score),
                "outlook_score": round_to_quarter(outlook_score),
                "status": status,
                "workspace": workspace_name,
                "last_check": {
                    "id": str(check_id),
                    "date": check_date.isoformat() if check_date else None
                },
                "reports_link": reports_link,
                "history": {
                    "total_checks": total_checks,
                    "good_checks": good_checks,
                    "bad_checks": bad_checks
                },
                "bounce_count": bounce_count,
                "reply_count": reply_count,
                "emails_sent": emails_sent
            })
        
        return {
            "data": data,
            "meta": {
                "total": total_count,
                "page": page,
                "per_page": per_page,
                "total_pages": total_pages
            }
        }

@router.post("/create-spamcheck-bison", auth=AuthBearer())
def create_spamcheck_bison(request, payload: CreateSpamcheckBisonSchema):
    """
    Create a new spamcheck with accounts for Bison
    
    Parameters:
        - name: Name of the spamcheck
        - user_organization_id: ID of the Bison organization to use
        - accounts: List of email accounts to check (e.g. ["test1@example.com", "test2@example.com"])
        - text_only: Whether to send text-only emails
        - subject: Email subject template
        - body: Email body template
        - scheduled_at: When to run the spamcheck
        - recurring_days: Optional, number of days for recurring checks
        - weekdays: Optional, list of weekdays (0=Monday, 6=Sunday) when this spamcheck should run
        - is_domain_based: Whether to filter accounts by domain
        - conditions: Optional, conditions for sending
        - reports_waiting_time: Optional, reports waiting time
        - update_sending_limit: Whether to update sending limits in Bison API based on scores
    """
    user = request.auth
    
    # Validate accounts list is not empty
    if not payload.accounts:
        return {
            "success": False,
            "message": "At least one email account is required"
        }
    
    # Get the specific organization with better error handling
    try:
        user_organization = UserBison.objects.get(
            id=payload.user_organization_id,
            user=user
        )
        
        if not user_organization.bison_organization_status:
            return {
                "success": False,
                "message": f"Organization with ID {payload.user_organization_id} exists but is not active. Please activate it first."
            }
            
    except ObjectDoesNotExist:
        return {
            "success": False,
            "message": f"Organization with ID {payload.user_organization_id} not found. Please check if the organization ID is correct and belongs to your account."
        }
    
    # Check if spamcheck with same name exists
    existing_spamcheck = UserSpamcheckBison.objects.filter(
        user=user,
        user_organization=user_organization,
        name=payload.name
    ).first()
    
    if existing_spamcheck:
        return {
            "success": False,
            "message": f"A spamcheck with the name '{payload.name}' already exists for this organization. Please use a different name."
        }
    
    try:
        with transaction.atomic():
            # Create spamcheck
            spamcheck = UserSpamcheckBison.objects.create(
                user=user,
                user_organization=user_organization,
                name=payload.name,
                status='queued',  # Explicitly set status to queued
                scheduled_at=payload.scheduled_at,
                recurring_days=payload.recurring_days,
                weekdays=','.join(map(str, payload.weekdays)) if payload.weekdays else None,
                is_domain_based=payload.is_domain_based,
                conditions=payload.conditions,
                reports_waiting_time=payload.reports_waiting_time,
                update_sending_limit=payload.update_sending_limit,
                plain_text=payload.text_only,
                subject=payload.subject,
                body=payload.body
            )
            
            # Create accounts
            accounts = []
            for email in payload.accounts:
                account = UserSpamcheckAccountsBison.objects.create(
                    user=user,
                    organization=user_organization,
                    bison_spamcheck=spamcheck,
                    email_account=email
                )
                accounts.append(account)
            
            return {
                "success": True,
                "message": "Spamcheck created successfully",
                "data": {
                    "id": spamcheck.id,
                    "name": spamcheck.name,
                    "status": spamcheck.status,
                    "accounts_count": len(accounts)
                }
            }
            
    except Exception as e:
        log_to_terminal("Spamcheck", "Create Bison", f"Error creating spamcheck: {str(e)}")
        return {
            "success": False,
            "message": f"Error creating spamcheck: {str(e)}"
        }

@router.put("/update-spamcheck-bison/{spamcheck_id}", auth=AuthBearer())
def update_spamcheck_bison(request, spamcheck_id: int, payload: UpdateSpamcheckBisonSchema):
    """
    Update an existing Bison spamcheck
    
    Parameters:
        - spamcheck_id: ID of the spamcheck to update
        - name: Optional, new name for the spamcheck
        - accounts: Optional, new list of email accounts
        - text_only: Optional, whether to send text-only emails
        - subject: Optional, new email subject template
        - body: Optional, new email body template
        - scheduled_at: Optional, new scheduled time
        - recurring_days: Optional, new recurring days setting
        - weekdays: Optional, list of weekdays (0=Monday, 6=Sunday) when this spamcheck should run
        - is_domain_based: Optional, whether to filter accounts by domain
        - conditions: Optional, conditions for sending
        - reports_waiting_time: Optional, reports waiting time
        - update_sending_limit: Optional, whether to update sending limits in Bison API based on scores
    """
    user = request.auth
    log_to_terminal("Spamcheck", "Update Bison", f"Starting update for spamcheck ID {spamcheck_id}")
    log_to_terminal("Spamcheck", "Update Bison", f"Payload received: {payload.dict()}")
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = UserSpamcheckBison.objects.get(
            id=spamcheck_id,
            user=user
        )
        log_to_terminal("Spamcheck", "Update Bison", f"Found spamcheck: {spamcheck.name} with status {spamcheck.status}")
        
        # Check if status allows updates
        if spamcheck.status not in ['queued', 'pending', 'failed', 'completed', 'paused', 'waiting_for_reports']:
            log_to_terminal("Spamcheck", "Update Bison", f"Cannot update spamcheck with status '{spamcheck.status}'")
            return {
                "success": False,
                "message": f"Cannot update spamcheck with status '{spamcheck.status}'. Only queued, pending, failed, completed, paused, or waiting_for_reports spamchecks can be updated."
            }
        
        try:
            with transaction.atomic():
                log_to_terminal("Spamcheck", "Update Bison", "Starting transaction for update")
                
                # For simple updates (just subject/body/text_only), use direct SQL update to avoid triggering signals
                if (payload.subject is not None or payload.body is not None or payload.text_only is not None) and \
                   payload.name is None and \
                   payload.scheduled_at is None and \
                   payload.recurring_days is None and \
                   payload.weekdays is None and \
                   payload.is_domain_based is None and \
                   payload.conditions is None and \
                   payload.reports_waiting_time is None and \
                   payload.update_sending_limit is None and \
                   payload.accounts is None:
                    
                    log_to_terminal("Spamcheck", "Update Bison", "Performing simple update with direct SQL")
                    
                    # Build update fields dictionary
                    update_fields_dict = {}
                    
                    if payload.subject is not None:
                        log_to_terminal("Spamcheck", "Update Bison", f"Will update subject")
                        update_fields_dict['subject'] = payload.subject
                    
                    if payload.body is not None:
                        log_to_terminal("Spamcheck", "Update Bison", f"Will update body (length: {len(payload.body)})")
                        update_fields_dict['body'] = payload.body
                    
                    if payload.text_only is not None:
                        log_to_terminal("Spamcheck", "Update Bison", f"Will update plain_text to '{payload.text_only}'")
                        update_fields_dict['plain_text'] = payload.text_only
                    
                    # Always update updated_at
                    update_fields_dict['updated_at'] = timezone.now()
                    
                    # Use update() method which translates to a direct SQL UPDATE
                    rows_updated = UserSpamcheckBison.objects.filter(id=spamcheck_id).update(**update_fields_dict)
                    
                    log_to_terminal("Spamcheck", "Update Bison", f"Simple update completed, {rows_updated} rows affected")
                    
                    # Refresh our spamcheck object to get the updated values
                    spamcheck.refresh_from_db()
                    
                    return {
                        "success": True,
                        "message": "Spamcheck updated successfully with direct SQL",
                        "data": {
                            "id": spamcheck.id,
                            "name": spamcheck.name,
                            "status": spamcheck.status,
                            "fields_updated": list(update_fields_dict.keys())
                        }
                    }
                
                # For complex updates, use the normal ORM approach with update_fields
                log_to_terminal("Spamcheck", "Update Bison", "Performing complex update with ORM")
                
                # Track which fields to update
                update_fields = []
                
                # Update spamcheck fields if provided
                if payload.name is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating name from '{spamcheck.name}' to '{payload.name}'")
                    spamcheck.name = payload.name
                    update_fields.append('name')
                if payload.scheduled_at is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating scheduled_at to '{payload.scheduled_at}'")
                    spamcheck.scheduled_at = payload.scheduled_at
                    update_fields.append('scheduled_at')
                if payload.recurring_days is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating recurring_days to '{payload.recurring_days}'")
                    spamcheck.recurring_days = payload.recurring_days
                    update_fields.append('recurring_days')
                if payload.weekdays is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating weekdays to '{payload.weekdays}'")
                    spamcheck.weekdays = ','.join(map(str, payload.weekdays)) if payload.weekdays else None
                    update_fields.append('weekdays')
                if payload.is_domain_based is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating is_domain_based to '{payload.is_domain_based}'")
                    spamcheck.is_domain_based = payload.is_domain_based
                    update_fields.append('is_domain_based')
                if payload.conditions is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating conditions to '{payload.conditions}'")
                    spamcheck.conditions = payload.conditions
                    update_fields.append('conditions')
                if payload.reports_waiting_time is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating reports_waiting_time to '{payload.reports_waiting_time}'")
                    spamcheck.reports_waiting_time = payload.reports_waiting_time
                    update_fields.append('reports_waiting_time')
                if payload.update_sending_limit is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating update_sending_limit to '{payload.update_sending_limit}'")
                    spamcheck.update_sending_limit = payload.update_sending_limit
                    update_fields.append('update_sending_limit')
                if payload.text_only is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating plain_text to '{payload.text_only}'")
                    spamcheck.plain_text = payload.text_only
                    update_fields.append('plain_text')
                if payload.subject is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating subject to '{payload.subject}'")
                    spamcheck.subject = payload.subject
                    update_fields.append('subject')
                if payload.body is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating body (length: {len(payload.body)})")
                    spamcheck.body = payload.body
                    update_fields.append('body')
                
                # Always update updated_at
                update_fields.append('updated_at')
                
                log_to_terminal("Spamcheck", "Update Bison", f"About to save spamcheck changes with fields: {update_fields}")
                spamcheck.save(update_fields=update_fields)
                log_to_terminal("Spamcheck", "Update Bison", "Spamcheck saved successfully")
                
                # Update accounts if provided
                accounts_updated = False
                if payload.accounts is not None:
                    log_to_terminal("Spamcheck", "Update Bison", f"Updating accounts list with {len(payload.accounts)} accounts")
                    # Delete existing accounts
                    existing_count = UserSpamcheckAccountsBison.objects.filter(bison_spamcheck=spamcheck).count()
                    log_to_terminal("Spamcheck", "Update Bison", f"Deleting {existing_count} existing accounts")
                    UserSpamcheckAccountsBison.objects.filter(bison_spamcheck=spamcheck).delete()
                    
                    # Create new accounts
                    for email in payload.accounts:
                        log_to_terminal("Spamcheck", "Update Bison", f"Adding account: {email}")
                        UserSpamcheckAccountsBison.objects.create(
                            user=user,
                            organization=spamcheck.user_organization,
                            bison_spamcheck=spamcheck,
                            email_account=email
                        )
                    accounts_updated = True
                    log_to_terminal("Spamcheck", "Update Bison", "Accounts updated successfully")
                
                log_to_terminal("Spamcheck", "Update Bison", "Update completed successfully")
                return {
                    "success": True,
                    "message": "Spamcheck updated successfully",
                    "data": {
                        "id": spamcheck.id,
                        "name": spamcheck.name,
                        "scheduled_at": spamcheck.scheduled_at,
                        "recurring_days": spamcheck.recurring_days,
                        "status": spamcheck.status,
                        "accounts_updated": accounts_updated,
                        "fields_updated": update_fields
                    }
                }
                
        except IntegrityError as ie:
            log_to_terminal("Spamcheck", "Update Bison", f"IntegrityError: {str(ie)}")
            return {
                "success": False,
                "message": f"A spamcheck with the name '{payload.name}' already exists for this organization. Please use a different name."
            }
            
    except UserSpamcheckBison.DoesNotExist:
        log_to_terminal("Spamcheck", "Update Bison", f"Spamcheck with ID {spamcheck_id} not found")
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to update it."
        }
    except Exception as e:
        log_to_terminal("Spamcheck", "Update Bison", f"Error updating spamcheck: {str(e)}")
        return {
            "success": False,
            "message": f"Error updating spamcheck: {str(e)}"
        }

@router.delete("/delete-spamcheck-bison/{spamcheck_id}", auth=AuthBearer())
def delete_spamcheck_bison(request, spamcheck_id: int):
    """
    Delete a Bison spamcheck and all its related data
    
    Parameters:
        - spamcheck_id: ID of the spamcheck to delete
    """
    user = request.auth
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = UserSpamcheckBison.objects.get(
            id=spamcheck_id,
            user=user
        )
        
        # Check if status allows deletion
        if spamcheck.status in ['in_progress', 'generating_reports', 'waiting_for_reports']:
            return {
                "success": False,
                "message": f"Cannot delete spamcheck with status '{spamcheck.status}'. Only spamchecks that are not in progress, waiting for reports, or generating reports can be deleted."
            }
        
        # Store name for response
        spamcheck_name = spamcheck.name
        
        # Delete the spamcheck (this will cascade delete accounts)
        spamcheck.delete()
        
        return {
            "success": True,
            "message": f"Spamcheck '{spamcheck_name}' and all related data deleted successfully"
        }
        
    except UserSpamcheckBison.DoesNotExist:
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to delete it."
        }
    except Exception as e:
        log_to_terminal("Spamcheck", "Delete Bison", f"Error deleting spamcheck: {str(e)}")
        return {
            "success": False,
            "message": f"Error deleting spamcheck: {str(e)}"
        }

@router.post("/toggle-pause-bison/{spamcheck_id}", auth=AuthBearer())
def toggle_pause_spamcheck_bison(request, spamcheck_id: int):
    """
    Toggle Bison spamcheck between paused and queued status.
    Only works if current status is paused, queued, pending, or completed.
    """
    user = request.auth
    log_to_terminal("Spamcheck", "TogglePause", f"User {user.email} toggling pause for Bison spamcheck ID {spamcheck_id}")
    
    try:
        # Get the spamcheck and verify ownership
        spamcheck = get_object_or_404(UserSpamcheckBison, id=spamcheck_id, user=user)
        
        # Check if status allows toggling
        if spamcheck.status not in ['queued', 'pending', 'paused', 'completed']:
            log_to_terminal("Spamcheck", "TogglePause", f"Cannot toggle pause for spamcheck with status '{spamcheck.status}'")
            return {
                "success": False,
                "message": f"Cannot toggle pause for spamcheck with status '{spamcheck.status}'. Only queued, pending, paused, or completed spamchecks can be toggled."
            }
        
        # Toggle status
        new_status = 'paused' if spamcheck.status in ['queued', 'pending', 'completed'] else 'queued'
        spamcheck.status = new_status
        spamcheck.save()
        
        log_to_terminal("Spamcheck", "TogglePause", f"Successfully toggled Bison spamcheck '{spamcheck.name}' to {new_status}")
        
        return {
            "success": True,
            "message": f"Spamcheck '{spamcheck.name}' is now {new_status}",
            "data": {
                "id": spamcheck.id,
                "name": spamcheck.name,
                "status": new_status
            }
        }
        
    except UserSpamcheckBison.DoesNotExist:
        log_to_terminal("Spamcheck", "TogglePause", f"Bison spamcheck with ID {spamcheck_id} not found")
        return {
            "success": False,
            "message": f"Spamcheck with ID {spamcheck_id} not found or you don't have permission to modify it."
        }
    except Exception as e:
        log_to_terminal("Spamcheck", "TogglePause", f"Error toggling Bison spamcheck pause status: {str(e)}")
        return {
            "success": False,
            "message": f"Error toggling spamcheck pause status: {str(e)}"
        }

@router.get(
    "/get-spamcheck/{spamcheck_id}",
    response=SpamcheckBisonDetailsResponseSchema,
    auth=AuthBearer(),
    summary="Get Bison Spamcheck Details",
    description="Get detailed information about a specific Bison spamcheck by its ID"
)
def get_bison_spamcheck_details(request, spamcheck_id: int):
    """
    Get detailed information about a specific Bison spamcheck by its ID
    """
    try:
        # Get the spamcheck
        spamcheck = get_object_or_404(UserSpamcheckBison, id=spamcheck_id)
        
        # Check if the user has access to this spamcheck
        if spamcheck.user.id != request.auth.id:
            return {
                "success": False,
                "message": "You don't have permission to access this spamcheck",
                "data": None
            }
        
        # Get the latest reports for this spamcheck
        reports = UserSpamcheckBisonReport.objects.filter(spamcheck_bison=spamcheck)
        
        # Calculate summary statistics
        total_accounts = reports.count()
        inboxed_accounts = reports.filter(is_good=True).count()
        spam_accounts = total_accounts - inboxed_accounts
        
        # Calculate average scores
        google_score = 0
        outlook_score = 0
        
        if total_accounts > 0:
            google_score = sum(float(report.google_pro_score) for report in reports) / total_accounts * 100
            outlook_score = sum(float(report.outlook_pro_score) for report in reports) / total_accounts * 100
        
        # Get the last run date (latest report creation date)
        last_run_date = spamcheck.updated_at
        if reports.exists():
            last_run_date = reports.order_by('-created_at').first().created_at
        
        # Format waiting time
        waiting_time = f"{spamcheck.reports_waiting_time} hours"
        
        # Parse conditions for Google and Outlook inbox criteria
        google_inbox_criteria = "Not specified"
        outlook_inbox_criteria = "Not specified"
        
        if spamcheck.conditions:
            try:
                log_to_terminal("SpamCheck", "Debug", f"Parsing conditions: {spamcheck.conditions}")
                
                # Handle the case where conditions are joined without spaces
                # Example: "google>0.5andoutlook>0.3sending=25/1"
                conditions = spamcheck.conditions
                
                # Parse Google criteria
                if "google>" in conditions:
                    if "google>=" in conditions:
                        # Extract value after 'google>=' and before 'and' or end of string
                        start_idx = conditions.index("google>=") + len("google>=")
                        end_idx = conditions.find("and", start_idx) if "and" in conditions[start_idx:] else len(conditions)
                        value_str = conditions[start_idx:end_idx].strip()
                        # Clean the value string
                        value_str = ''.join(c for c in value_str if c.isdigit() or c == '.')
                        google_value = float(value_str) * 100
                        google_inbox_criteria = f"{google_value}% or higher"
                    elif "google>" in conditions:
                        # Extract value after 'google>' and before 'and' or end of string
                        start_idx = conditions.index("google>") + len("google>")
                        end_idx = conditions.find("and", start_idx) if "and" in conditions[start_idx:] else len(conditions)
                        value_str = conditions[start_idx:end_idx].strip()
                        # Clean the value string
                        value_str = ''.join(c for c in value_str if c.isdigit() or c == '.')
                        google_value = float(value_str) * 100
                        google_inbox_criteria = f"Above {google_value}%"
                
                # Parse Outlook criteria
                if "outlook>" in conditions:
                    if "outlook>=" in conditions:
                        # Extract value after 'outlook>=' and before 'and' or 'sending=' or end of string
                        start_idx = conditions.index("outlook>=") + len("outlook>=")
                        end_idx1 = conditions.find("and", start_idx)
                        end_idx2 = conditions.find("sending=", start_idx)
                        end_idx = min(end_idx1 if end_idx1 != -1 else len(conditions), 
                                      end_idx2 if end_idx2 != -1 else len(conditions))
                        value_str = conditions[start_idx:end_idx].strip()
                        # Clean the value string
                        value_str = ''.join(c for c in value_str if c.isdigit() or c == '.')
                        outlook_value = float(value_str) * 100
                        outlook_inbox_criteria = f"{outlook_value}% or higher"
                    elif "outlook>" in conditions:
                        # Extract value after 'outlook>' and before 'and' or 'sending=' or end of string
                        start_idx = conditions.index("outlook>") + len("outlook>")
                        end_idx1 = conditions.find("and", start_idx)
                        end_idx2 = conditions.find("sending=", start_idx)
                        end_idx = min(end_idx1 if end_idx1 != -1 else len(conditions), 
                                      end_idx2 if end_idx2 != -1 else len(conditions))
                        value_str = conditions[start_idx:end_idx].strip()
                        # Clean the value string
                        value_str = ''.join(c for c in value_str if c.isdigit() or c == '.')
                        outlook_value = float(value_str) * 100
                        outlook_inbox_criteria = f"Above {outlook_value}%"
                
                log_to_terminal("SpamCheck", "Debug", f"Parsed Google criteria: {google_inbox_criteria}")
                log_to_terminal("SpamCheck", "Debug", f"Parsed Outlook criteria: {outlook_inbox_criteria}")
                
            except Exception as e:
                log_to_terminal("SpamCheck", "Warning", f"Error parsing conditions: {str(e)}")
                google_inbox_criteria = "Custom criteria"
                outlook_inbox_criteria = "Custom criteria"
        
        # Construct the response
        response_data = {
            "id": str(spamcheck.id),
            "name": spamcheck.name,
            "createdAt": spamcheck.created_at.isoformat(),
            "lastRunDate": last_run_date.isoformat(),
            "status": spamcheck.status,
            "configuration": {
                "domainBased": spamcheck.is_domain_based,
                "trackOpens": False,  # Placeholder, adjust if you track this
                "trackClicks": False,  # Placeholder, adjust if you track this
                "waitingTime": waiting_time,
                "googleInboxCriteria": google_inbox_criteria,
                "outlookInboxCriteria": outlook_inbox_criteria,
                "updateSendingLimit": spamcheck.update_sending_limit,
                "weekdays": spamcheck.weekdays.split(',') if spamcheck.weekdays else None,
                "text_only": spamcheck.plain_text,
                "conditions": spamcheck.conditions
            },
            "emailContent": {
                "subject": spamcheck.subject,
                "body": spamcheck.body
            },
            "results": {
                "googleScore": round(google_score, 2),
                "outlookScore": round(outlook_score, 2),
                "totalAccounts": total_accounts,
                "inboxedAccounts": inboxed_accounts,
                "spamAccounts": spam_accounts
            }
        }
        
        return {
            "success": True,
            "message": "Spam check details retrieved successfully",
            "data": response_data
        }
        
    except Exception as e:
        log_to_terminal("SpamCheck", "Error", f"Error getting spamcheck details: {str(e)}")
        # Return a valid response structure even in case of error
        return {
            "success": False,
            "message": f"Error retrieving spam check details: {str(e)}",
            "data": {
                "id": str(spamcheck_id),
                "name": "Error retrieving spamcheck",
                "createdAt": timezone.now().isoformat(),
                "lastRunDate": timezone.now().isoformat(),
                "status": "error",
                "configuration": {
                    "domainBased": False,
                    "trackOpens": False,
                    "trackClicks": False,
                    "waitingTime": "N/A",
                    "googleInboxCriteria": "N/A",
                    "outlookInboxCriteria": "N/A",
                    "updateSendingLimit": False,
                    "weekdays": None,
                    "text_only": False,
                    "conditions": None
                },
                "emailContent": {
                    "subject": "",
                    "body": ""
                },
                "results": {
                    "googleScore": 0,
                    "outlookScore": 0,
                    "totalAccounts": 0,
                    "inboxedAccounts": 0,
                    "spamAccounts": 0
                }
            }
        }

@router.get(
    "/get-spamcheck-reports/{spamcheck_id}",
    response=BisonAccountsReportsResponseSchema,
    auth=AuthBearer(),
    summary="Get Bison Spamcheck Account Reports",
    description="Get reports for all accounts related to a specific Bison spamcheck"
)
def get_bison_spamcheck_reports(request, spamcheck_id: int):
    """
    Get reports for all accounts related to a specific Bison spamcheck
    """
    try:
        # Get the spamcheck
        spamcheck = get_object_or_404(UserSpamcheckBison, id=spamcheck_id)
        
        # Check if the user has access to this spamcheck
        if spamcheck.user.id != request.auth.id:
            return {
                "success": False,
                "message": "You don't have permission to access this spamcheck",
                "data": []
            }
        
        # Get all reports for this spamcheck
        reports = UserSpamcheckBisonReport.objects.filter(spamcheck_bison=spamcheck).order_by('-created_at')
        
        # Format the reports data
        reports_data = []
        for report in reports:
            # Determine status based on is_good flag
            status = "inbox" if report.is_good else "spam"
            
            reports_data.append({
                "id": str(report.id),
                "email": report.email_account,
                "googleScore": float(report.google_pro_score) * 100,  # Convert to percentage
                "outlookScore": float(report.outlook_pro_score) * 100,  # Convert to percentage
                "status": status,
                "reportLink": report.report_link,
                "createdAt": report.created_at.isoformat()
            })
        
        return {
            "success": True,
            "message": "Spam check reports retrieved successfully",
            "data": reports_data
        }
        
    except Exception as e:
        log_to_terminal("SpamCheck", "Error", f"Error getting spamcheck reports: {str(e)}")
        # Always return a valid data structure, even if it's an empty list
        return {
            "success": False,
            "message": f"Error retrieving spam check reports: {str(e)}",
            "data": []
        }

@router.get(
    "/account-bison-details",
    response=BisonAccountDetailsResponseSchema,
    auth=AuthBearer(),
    summary="Get Bison Account Details",
    description="Get detailed information for a specific Bison email account"
)
def get_bison_account_details(
    request,
    email: str
):
    """
    Get detailed information for a specific Bison email account.
    
    Args:
        email: The email address to get details for
    """
    try:
        user = request.auth
        
        # Get the latest check for this account
        query = """
            WITH latest_check AS (
                SELECT 
                    usbr.*,
                    ub.bison_organization_name,
                    ROW_NUMBER() OVER (
                        PARTITION BY usbr.email_account 
                        ORDER BY usbr.created_at DESC
                    ) as rn
                FROM user_spamcheck_bison_reports usbr
                JOIN user_bison ub ON usbr.bison_organization_id = ub.id
                JOIN user_spamcheck_bison usb ON usbr.spamcheck_bison_id = usb.id
                WHERE ub.user_id = %s AND usbr.email_account = %s
                AND usb.update_sending_limit = TRUE
            ),
            account_stats AS (
                SELECT 
                    usbr.email_account,
                    COUNT(*) as total_checks,
                    SUM(CASE WHEN usbr.is_good = TRUE THEN 1 ELSE 0 END) as good_checks,
                    SUM(CASE WHEN usbr.is_good = FALSE THEN 1 ELSE 0 END) as bad_checks
                FROM user_spamcheck_bison_reports usbr
                JOIN user_spamcheck_bison usb ON usbr.spamcheck_bison_id = usb.id
                WHERE usbr.bison_organization_id IN (SELECT id FROM user_bison WHERE user_id = %s)
                AND usbr.email_account = %s
                AND usb.update_sending_limit = TRUE
                GROUP BY usbr.email_account
            )
            SELECT 
                lc.email_account,
                SUBSTRING_INDEX(lc.email_account, '@', -1) as domain,
                COALESCE(lc.sending_limit, 25) as sends_per_day,
                lc.google_pro_score as google_score,
                lc.outlook_pro_score as outlook_score,
                CASE 
                    WHEN lc.is_good THEN 'Inboxing'
                    ELSE 'Resting'
                END as status,
                lc.bison_organization_name as workspace,
                lc.id as check_id,
                lc.created_at as check_date,
                lc.report_link as reports_link,
                COALESCE(ast.total_checks, 0) as total_checks,
                COALESCE(ast.good_checks, 0) as good_checks,
                COALESCE(ast.bad_checks, 0) as bad_checks,
                COALESCE(lc.bounced_count, 0) as bounce_count,
                COALESCE(lc.unique_replied_count, 0) as reply_count,
                COALESCE(lc.emails_sent_count, 0) as emails_sent,
                lc.tags_list
            FROM latest_check lc
            LEFT JOIN account_stats ast ON lc.email_account = ast.email_account
            WHERE lc.rn = 1
        """
        
        with connection.cursor() as cursor:
            cursor.execute(query, [user.id, email, user.id, email])
            columns = [col[0] for col in cursor.description]
            row = cursor.fetchone()
            
            if not row:
                return {
                    "success": False,
                    "message": f"No data found for email: {email}",
                    "data": None
                }
            
            # Get the account details
            account_data = dict(zip(columns, row))
            
            # Convert tags_list from string to array if it exists
            tags_list = None
            if account_data.get('tags_list'):
                try:
                    # Try to parse as JSON first
                    tags_list = json.loads(account_data['tags_list'])
                except json.JSONDecodeError:
                    # If not JSON, split by comma
                    tags_list = [tag.strip() for tag in account_data['tags_list'].split(',') if tag.strip()]
            
            # Get historical score data for charts
            history_query = """
                SELECT 
                    usbr.id,
                    usbr.created_at as date,
                    usbr.google_pro_score as google_score,
                    usbr.outlook_pro_score as outlook_score,
                    CASE 
                        WHEN usbr.is_good THEN 'Inboxing'
                        ELSE 'Resting'
                    END as status,
                    usbr.report_link
                FROM user_spamcheck_bison_reports usbr
                JOIN user_spamcheck_bison usb ON usbr.spamcheck_bison_id = usb.id
                WHERE usbr.bison_organization_id IN (SELECT id FROM user_bison WHERE user_id = %s)
                AND usbr.email_account = %s
                AND usb.update_sending_limit = TRUE
                ORDER BY usbr.created_at DESC
                LIMIT 10
            """
            
            cursor.execute(history_query, [user.id, email])
            history_columns = [col[0] for col in cursor.description]
            history_rows = cursor.fetchall()
            
            score_history = []
            for history_row in history_rows:
                history_data = dict(zip(history_columns, history_row))
                score_history.append({
                    "date": history_data['date'].isoformat() if history_data['date'] else None,
                    "google_score": round_to_quarter(history_data['google_score']),
                    "outlook_score": round_to_quarter(history_data['outlook_score']),
                    "status": history_data['status'],
                    "report_link": history_data['report_link']
                })
            
            # Get other accounts with the same domain
            domain = account_data['domain']
            domain_accounts_query = """
                WITH latest_domain_checks AS (
                    SELECT 
                        usbr.*,
                        ub.bison_organization_name,
                        ROW_NUMBER() OVER (
                            PARTITION BY usbr.email_account 
                            ORDER BY usbr.created_at DESC
                        ) as rn
                    FROM user_spamcheck_bison_reports usbr
                    JOIN user_bison ub ON usbr.bison_organization_id = ub.id
                    WHERE ub.user_id = %s 
                    AND SUBSTRING_INDEX(usbr.email_account, '@', -1) = %s
                    AND usbr.email_account != %s
                ),
                account_stats AS (
                    SELECT 
                        email_account,
                        COUNT(*) as total_checks,
                        SUM(CASE WHEN is_good = TRUE THEN 1 ELSE 0 END) as good_checks,
                        SUM(CASE WHEN is_good = FALSE THEN 1 ELSE 0 END) as bad_checks
                    FROM user_spamcheck_bison_reports
                    WHERE bison_organization_id IN (SELECT id FROM user_bison WHERE user_id = %s)
                    AND SUBSTRING_INDEX(email_account, '@', -1) = %s
                    AND email_account != %s
                    GROUP BY email_account
                )
                SELECT 
                    ldc.email_account,
                    ldc.google_pro_score as google_score,
                    ldc.outlook_pro_score as outlook_score,
                    CASE 
                        WHEN ldc.is_good THEN 'Inboxing'
                        ELSE 'Resting'
                    END as status,
                    ldc.bison_organization_name as workspace,
                    ldc.created_at as last_check_date,
                    COALESCE(ldc.bounced_count, 0) as bounce_count,
                    COALESCE(ldc.unique_replied_count, 0) as reply_count,
                    COALESCE(ldc.emails_sent_count, 0) as emails_sent,
                    COALESCE(ast.total_checks, 0) as total_checks,
                    COALESCE(ast.good_checks, 0) as good_checks,
                    COALESCE(ast.bad_checks, 0) as bad_checks
                FROM latest_domain_checks ldc
                LEFT JOIN account_stats ast ON ldc.email_account = ast.email_account
                WHERE ldc.rn = 1
                ORDER BY ldc.created_at DESC
                LIMIT 10
            """
            
            cursor.execute(domain_accounts_query, [user.id, domain, email, user.id, domain, email])
            domain_columns = [col[0] for col in cursor.description]
            domain_rows = cursor.fetchall()
            
            domain_accounts = []
            for domain_row in domain_rows:
                domain_data = dict(zip(domain_columns, domain_row))
                domain_accounts.append({
                    "email": domain_data['email_account'],
                    "google_score": round_to_quarter(domain_data['google_score']),
                    "outlook_score": round_to_quarter(domain_data['outlook_score']),
                    "status": domain_data['status'],
                    "workspace": domain_data['workspace'],
                    "last_check_date": domain_data['last_check_date'].isoformat() if domain_data['last_check_date'] else None,
                    "bounce_count": domain_data['bounce_count'],
                    "reply_count": domain_data['reply_count'],
                    "emails_sent": domain_data['emails_sent'],
                    "history": {
                        "total_checks": domain_data['total_checks'],
                        "good_checks": domain_data['good_checks'],
                        "bad_checks": domain_data['bad_checks']
                    }
                })
            
            # Get domain performance summary
            domain_summary_query = """
                WITH domain_checks AS (
                    SELECT 
                        SUBSTRING_INDEX(email_account, '@', -1) as domain,
                        COUNT(DISTINCT email_account) as total_accounts,
                        AVG(google_pro_score) as avg_google_score,
                        AVG(outlook_pro_score) as avg_outlook_score,
                        SUM(CASE WHEN is_good = TRUE THEN 1 ELSE 0 END) as inboxing_accounts,
                        SUM(CASE WHEN is_good = FALSE THEN 1 ELSE 0 END) as resting_accounts,
                        COUNT(*) as total_checks,
                        SUM(CASE WHEN is_good = TRUE THEN 1 ELSE 0 END) as good_checks,
                        SUM(CASE WHEN is_good = FALSE THEN 1 ELSE 0 END) as bad_checks
                    FROM (
                        SELECT 
                            usbr.email_account,
                            usbr.google_pro_score,
                            usbr.outlook_pro_score,
                            usbr.is_good,
                            ROW_NUMBER() OVER (
                                PARTITION BY usbr.email_account 
                                ORDER BY usbr.created_at DESC
                            ) as rn
                        FROM user_spamcheck_bison_reports usbr
                        JOIN user_bison ub ON usbr.bison_organization_id = ub.id
                        WHERE ub.user_id = %s
                        AND SUBSTRING_INDEX(usbr.email_account, '@', -1) = %s
                    ) latest_checks
                    WHERE rn = 1
                    GROUP BY domain
                ),
                domain_check_stats AS (
                    SELECT 
                        SUBSTRING_INDEX(email_account, '@', -1) as domain,
                        COUNT(*) as total_domain_checks,
                        SUM(CASE WHEN is_good = TRUE THEN 1 ELSE 0 END) as good_domain_checks,
                        SUM(CASE WHEN is_good = FALSE THEN 1 ELSE 0 END) as bad_domain_checks
                    FROM user_spamcheck_bison_reports
                    WHERE bison_organization_id IN (SELECT id FROM user_bison WHERE user_id = %s)
                    AND SUBSTRING_INDEX(email_account, '@', -1) = %s
                    GROUP BY domain
                )
                SELECT 
                    dc.domain,
                    dc.total_accounts,
                    dc.avg_google_score,
                    dc.avg_outlook_score,
                    dc.inboxing_accounts,
                    dc.resting_accounts,
                    dc.total_checks,
                    dc.good_checks,
                    dc.bad_checks,
                    dcs.total_domain_checks,
                    dcs.good_domain_checks,
                    dcs.bad_domain_checks
                FROM domain_checks dc
                LEFT JOIN domain_check_stats dcs ON dc.domain = dcs.domain
            """
            
            cursor.execute(domain_summary_query, [user.id, domain, user.id, domain])
            summary_columns = [col[0] for col in cursor.description]
            summary_row = cursor.fetchone()
            
            domain_summary = {}
            if summary_row:
                domain_summary = dict(zip(summary_columns, summary_row))
        
        # Format the response
        return {
            "success": True,
            "message": "Account details retrieved successfully",
            "data": {
                "email": account_data['email_account'],
                "domain": account_data['domain'],
                "sends_per_day": account_data['sends_per_day'],
                "google_score": round_to_quarter(account_data['google_score']),
                "outlook_score": round_to_quarter(account_data['outlook_score']),
                "status": account_data['status'],
                "workspace": account_data['workspace'],
                "last_check": {
                    "id": str(account_data['check_id']),
                    "date": account_data['check_date'].isoformat() if account_data['check_date'] else None
                },
                "reports_link": account_data['reports_link'],
                "history": {
                    "total_checks": account_data['total_checks'],
                    "good_checks": account_data['good_checks'],
                    "bad_checks": account_data['bad_checks']
                },
                "bounce_count": account_data['bounce_count'],
                "reply_count": account_data['reply_count'],
                "emails_sent": account_data['emails_sent'],
                "tags_list": tags_list,
                "score_history": score_history,
                "domain_accounts": domain_accounts,
                "domain_summary": {
                    "total_accounts": domain_summary.get('total_accounts', 0),
                    "avg_google_score": round_to_quarter(domain_summary.get('avg_google_score', 0)),
                    "avg_outlook_score": round_to_quarter(domain_summary.get('avg_outlook_score', 0)),
                    "inboxing_accounts": domain_summary.get('inboxing_accounts', 0),
                    "resting_accounts": domain_summary.get('resting_accounts', 0),
                    "total_checks": domain_summary.get('total_domain_checks', 0),
                    "good_checks": domain_summary.get('good_domain_checks', 0),
                    "bad_checks": domain_summary.get('bad_domain_checks', 0)
                }
            }
        }
        
    except Exception as e:
        log_to_terminal("SpamCheck", "Error", f"Error getting account details: {str(e)}")
        return {
            "success": False,
            "message": f"Error retrieving account details: {str(e)}",
            "data": None
        }

@router.get(
    "/fetch-campaign-copy-bison/{campaign_id}", 
    auth=AuthBearer(),
    response=CampaignCopyResponse,
    summary="Fetch Bison Campaign Copy",
    description="Fetch email subject and body from a Bison campaign's first sequence step"
)
def fetch_campaign_copy(request, campaign_id: str):
    """
    Fetch email copy (subject and body) from a campaign
    
    Parameters:
        - campaign_id: ID of the campaign to fetch copy from
    """
    user = request.auth
    log_to_terminal("Spamcheck", "FetchCopy", f"Fetching copy for campaign ID {campaign_id}")
    
    try:
        # Get the user's Bison organization - handle multiple organizations
        user_bison_orgs = UserBison.objects.filter(
            user=user,
            bison_organization_status=True  # Make sure organization is active
        )
        
        if not user_bison_orgs.exists():
            log_to_terminal("Spamcheck", "FetchCopy", f"User {user.email} has no active Bison organization")
            return {
                "success": False,
                "message": "You don't have an active Bison organization configured",
                "data": {
                    "subject": "",
                    "body": "",
                    "campaign_id": campaign_id
                }
            }
        
        # Use the first active organization
        user_bison = user_bison_orgs.first()
        log_to_terminal("Spamcheck", "FetchCopy", f"Using Bison organization: {user_bison.bison_organization_name}")
        
        # Make API request to fetch campaign sequence steps
        api_url = f"{user_bison.base_url.rstrip('/')}/api/campaigns/{campaign_id}/sequence-steps"
        log_to_terminal("Spamcheck", "FetchCopy", f"Making API request to: {api_url}")
        
        response = requests.get(
            api_url,
            headers={
                "Authorization": f"Bearer {user_bison.bison_organization_api_key}",
                "Content-Type": "application/json"
            }
        )
        
        if response.status_code != 200:
            log_to_terminal("Spamcheck", "FetchCopy", f"API Error: {response.status_code} - {response.text}")
            return {
                "success": False,
                "message": f"Error fetching campaign copy: {response.text}",
                "data": {
                    "subject": "",
                    "body": "",
                    "campaign_id": campaign_id
                }
            }
        
        # Parse response
        data = response.json()
        steps = data.get('data', [])
        
        if not steps:
            log_to_terminal("Spamcheck", "FetchCopy", f"No sequence steps found for campaign {campaign_id}")
            return {
                "success": False,
                "message": "No sequence steps found for this campaign",
                "data": {
                    "subject": "",
                    "body": "",
                    "campaign_id": campaign_id
                }
            }
        
        # Get the first step (step 1)
        first_step = steps[0]
        subject = first_step.get('email_subject', '')
        html_body = first_step.get('email_body', '')
        
        # Convert HTML to plain text while preserving formatting
        try:
            import re
            from html import unescape
            
            # Function to convert HTML to formatted plain text
            def html_to_text(html):
                if not html:
                    return ""
                
                # Unescape HTML entities
                html = unescape(html)
                
                # Replace common block elements with newlines
                html = re.sub(r'</(div|p|h\d|ul|ol|li|blockquote|pre|table|tr)>', '\n', html)
                
                # Replace <br> tags with newlines
                html = re.sub(r'<br[^>]*>', '\n', html)
                
                # Replace multiple consecutive newlines with just two
                html = re.sub(r'\n{3,}', '\n\n', html)
                
                # Remove all remaining HTML tags
                html = re.sub(r'<[^>]*>', '', html)
                
                # Trim leading/trailing whitespace
                html = html.strip()
                
                return html
            
            # Convert the HTML body to plain text
            plain_body = html_to_text(html_body)
            log_to_terminal("Spamcheck", "FetchCopy", f"Successfully converted HTML to plain text")
            
        except Exception as e:
            log_to_terminal("Spamcheck", "FetchCopy", f"Error converting HTML to text: {str(e)}")
            plain_body = html_body  # Fallback to original HTML if conversion fails
        
        log_to_terminal("Spamcheck", "FetchCopy", f"Successfully fetched copy for campaign {campaign_id}")
        
        return {
            "success": True,
            "message": "Campaign copy fetched successfully",
            "data": {
                "subject": subject,
                "body": plain_body,
                "campaign_id": campaign_id
            }
        }
        
    except Exception as e:
        log_to_terminal("Spamcheck", "FetchCopy", f"Error fetching campaign copy: {str(e)}")
        return {
            "success": False,
            "message": f"Error fetching campaign copy: {str(e)}",
            "data": {
                "subject": "",
                "body": "",
                "campaign_id": campaign_id
            }
        }

class ContentSpamCheckSchema(Schema):
    """Schema for content spam check request"""
    content: str = Field(..., description="The content to check for spam")

class SpamCheckResultSchema(Schema):
    """Schema for spam check result"""
    is_spam: bool
    spam_score: float
    number_of_spam_words: int
    spam_words: List[str]
    comma_separated_spam_words: str

class SpamCheckResponseSchema(Schema):
    """Schema for spam check response"""
    success: bool
    message: str
    data: Dict[str, SpamCheckResultSchema]

@router.post(
    "/content-spam-check",
    auth=AuthBearer(),
    response=SpamCheckResponseSchema,
    summary="Check Content for Spam",
    description="Submits content to check for spam using the EmailGuard API"
)
def check_content_for_spam(request, payload: ContentSpamCheckSchema):
    """
    Check if content contains spam using EmailGuard API
    """
    try:
        user = request.auth
        
        # Get user's EmailGuard settings
        try:
            user_settings = UserSettings.objects.get(user=user)
            if not user_settings.emailguard_api_key:
                return {
                    "success": False,
                    "message": "EmailGuard API key not configured for this user",
                    "data": {
                        "message": {
                            "is_spam": False,
                            "spam_score": 0.0,
                            "number_of_spam_words": 0,
                            "spam_words": [],
                            "comma_separated_spam_words": ""
                        }
                    }
                }
            emailguard_api_key = user_settings.emailguard_api_key
        except UserSettings.DoesNotExist:
            return {
                "success": False,
                "message": "User settings not found",
                "data": {
                    "message": {
                        "is_spam": False,
                        "spam_score": 0.0,
                        "number_of_spam_words": 0,
                        "spam_words": [],
                        "comma_separated_spam_words": ""
                    }
                }
            }
        
        # Call EmailGuard API to check content
        api_url = "https://app.emailguard.io/api/v1/content-spam-check"
        
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {emailguard_api_key}",
                "Content-Type": "application/json"
            },
            json={"content": payload.content},
            timeout=30
        )
        
        # Print raw response for debugging
        print(f"EmailGuard API raw response: {response.text}")
        log_to_terminal("SpamCheck", "Debug", f"EmailGuard API raw response: {response.text}")
        
        if response.status_code != 200:
            return {
                "success": False,
                "message": f"Failed to check content with EmailGuard API: {response.text}",
                "data": {
                    "message": {
                        "is_spam": False,
                        "spam_score": 0.0,
                        "number_of_spam_words": 0,
                        "spam_words": [],
                        "comma_separated_spam_words": ""
                    }
                }
            }
        
        result = response.json()
        
        # Log the exact response for debugging
        print(f"EmailGuard API parsed response: {result}")
        log_to_terminal("SpamCheck", "Debug", f"EmailGuard API parsed response: {result}")
        
        # Handle the correct nested structure from EmailGuard API
        if "data" in result and "message" in result["data"]:
            message_data = result["data"]["message"]
            # Ensure all required fields are present with default values if missing
            formatted_message = {
                "is_spam": message_data.get("is_spam", False),
                "spam_score": message_data.get("spam_score", 0.0),
                "number_of_spam_words": message_data.get("number_of_spam_words", 0),
                "spam_words": message_data.get("spam_words", []),
                "comma_separated_spam_words": message_data.get("comma_separated_spam_words", "")
            }
            
            return {
                "success": True,
                "message": "Content spam check completed successfully",
                "data": {"message": formatted_message}
            }
        # Try other possible structures
        elif "message" in result:
            message_data = result["message"]
            # Ensure all required fields are present with default values if missing
            formatted_message = {
                "is_spam": message_data.get("is_spam", False),
                "spam_score": message_data.get("spam_score", 0.0),
                "number_of_spam_words": message_data.get("number_of_spam_words", 0),
                "spam_words": message_data.get("spam_words", []),
                "comma_separated_spam_words": message_data.get("comma_separated_spam_words", "")
            }
            
            return {
                "success": True,
                "message": "Content spam check completed successfully",
                "data": {"message": formatted_message}
            }
        # Try alternative response format
        elif "is_spam" in result:
            # The API might be returning the spam check result directly at the root level
            formatted_message = {
                "is_spam": result.get("is_spam", False),
                "spam_score": result.get("spam_score", 0.0),
                "number_of_spam_words": result.get("number_of_spam_words", 0),
                "spam_words": result.get("spam_words", []),
                "comma_separated_spam_words": result.get("comma_separated_spam_words", "")
            }
            
            return {
                "success": True,
                "message": "Content spam check completed successfully",
                "data": {"message": formatted_message}
            }
        else:
            # If the response doesn't have the expected structure, create a default response
            log_to_terminal("SpamCheck", "Warning", f"Unexpected response format from EmailGuard API: {result}")
            return {
                "success": True,
                "message": "Content spam check completed with unexpected response format",
                "data": {
                    "message": {
                        "is_spam": False,
                        "spam_score": 0.0,
                        "number_of_spam_words": 0,
                        "spam_words": [],
                        "comma_separated_spam_words": ""
                    }
                }
            }
        
    except Exception as e:
        log_to_terminal("SpamCheck", "Error", f"Error checking content for spam: {str(e)}")
        return {
            "success": False,
            "message": f"Error checking content for spam: {str(e)}",
            "data": {
                "message": {
                    "is_spam": False,
                    "spam_score": 0.0,
                    "number_of_spam_words": 0,
                    "spam_words": [],
                    "comma_separated_spam_words": ""
                }
            }
        }

def html_to_text(html):
    """Convert HTML to plain text while preserving some formatting"""
    # Remove style and script tags
    html = re.sub(r'<style.*?>.*?</style>', '', html, flags=re.DOTALL)
    html = re.sub(r'<script.*?>.*?</script>', '', html, flags=re.DOTALL)
    
    # Replace common tags with text equivalents
    html = html.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
    html = html.replace('</p>', '\n\n').replace('</div>', '\n')
    html = html.replace('</h1>', '\n\n').replace('</h2>', '\n\n').replace('</h3>', '\n\n')
    html = html.replace('</h4>', '\n\n').replace('</h5>', '\n\n').replace('</h6>', '\n\n')
    html = html.replace('<li>', '- ').replace('</li>', '\n')
    
    # Remove all remaining HTML tags
    html = re.sub(r'<.*?>', '', html)
    
    # Fix spacing
    html = re.sub(r' +', ' ', html)
    html = re.sub(r'\n+', '\n\n', html)
    
    # Decode HTML entities
    html = html.replace('&nbsp;', ' ')
    html = html.replace('&amp;', '&')
    html = html.replace('&lt;', '<')
    html = html.replace('&gt;', '>')
    html = html.replace('&quot;', '"')
    html = html.replace('&#39;', "'")
    
    return html.strip() 