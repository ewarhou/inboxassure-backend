from typing import List
from ninja import Router
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.core.exceptions import ObjectDoesNotExist
from authentication.authorization import AuthBearer
from .schema import CreateSpamcheckSchema, UpdateSpamcheckSchema, LaunchSpamcheckSchema, ListAccountsRequestSchema, ListAccountsResponseSchema
from .models import UserSpamcheck, UserSpamcheckAccounts, UserSpamcheckCampaignOptions, UserSpamcheckCampaigns, UserSpamcheckReport
from settings.models import UserInstantly, UserSettings
import requests
from django.conf import settings
from datetime import datetime
from django.utils import timezone
import pytz

router = Router(tags=["spamcheck"])

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
        if spamcheck.status not in ['draft', 'failed', 'completed']:
            return {
                "success": False,
                "message": f"Cannot delete spamcheck with status '{spamcheck.status}'. Only draft, failed, or completed spamchecks can be deleted."
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
            return {
                "success": False,
                "message": "User settings not found. Please configure API keys first."
            }
            
        # Verify user settings and tokens
        if not user_settings.emailguard_api_key:
            return {
                "success": False,
                "message": "EmailGuard API key not found. Please configure it first."
            }
            
        if not user_settings.instantly_user_token or not user_settings.instantly_status:
            return {
                "success": False,
                "message": "Instantly user token not found or inactive. Please reconnect your Instantly account."
            }
            
        # Verify organization status and tokens
        if not spamcheck.user_organization.instantly_organization_status:
            return {
                "success": False,
                "message": "Selected Instantly organization is not active. Please activate it first."
            }
            
        if not spamcheck.user_organization.instantly_organization_token:
            return {
                "success": False,
                "message": "Organization token not found. Please reconnect the organization."
            }
            
        # Start transaction and set status to in_progress at the beginning
        with transaction.atomic():
            # Update status to in_progress before starting
            spamcheck.status = 'in_progress'
            spamcheck.save()
            
            # Get accounts
            accounts = spamcheck.accounts.all()
            if not accounts:
                return {
                    "success": False,
                    "message": "No accounts found for this spamcheck."
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
                    
                    if emailguard_response.status_code not in [200, 201]:
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
                    print(f"Error processing account {account.email_account}: {str(e)}")
                    spamcheck.status = 'failed'
                    spamcheck.save()
                    return {
                        "success": False,
                        "message": f"Error processing account {account.email_account}: {str(e)}"
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
        return {
            "success": False,
            "message": f"Spamcheck with ID {payload.spamcheck_id} not found or you don't have permission to launch it."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error launching spamcheck: {str(e)}. Please try again or contact support if the issue persists."
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
            "message": f"Spamcheck {spamcheck_id} is now {new_status}",
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
    List accounts from Instantly API with filtering options
    """
    user = request.auth
    print("\n=== DEBUG: List Accounts Start ===")
    print(f"Parameters received:")
    print(f"- organization_id: {organization_id}")
    print(f"- search: {payload.search}")
    print(f"- ignore_tag: {payload.ignore_tag}")
    print(f"- is_active: {payload.is_active}")
    print(f"- limit: {payload.limit}")
    
    try:
        # Get the organization and verify ownership
        print("\nStep 1: Getting organization...")
        organization = get_object_or_404(UserInstantly, id=organization_id, user=user)
        print(f"✓ Found organization: {organization.instantly_organization_name}")
        
        # Get user settings
        user_settings = UserSettings.objects.get(user=user)
        
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
        
        print(f"\nAPI Response status: {response.status_code}")
        print("Raw API Response:")
        print(response.text)  # Print raw response for debugging
        
        if response.status_code != 200:
            print(f"✗ API Error: {response.text}")
            return {
                "success": False,
                "message": "Failed to fetch accounts from Instantly API",
                "data": {
                    "organization_id": organization_id,
                    "organization_name": organization.instantly_organization_name,
                    "total_accounts": 0,
                    "accounts": []
                }
            }
            
        response_data = response.json()
        accounts = response_data.get("accounts", [])
        
        # Extract just the emails and apply filters
        email_list = []
        for account in accounts:
            email = account.get("email")
            if not email:
                continue
                
            # Skip if account is not active (status != 1)
            if payload.is_active and account.get("status") != 1:
                continue
                
            # Skip if has ignored tag
            if payload.ignore_tag:
                account_tags = [tag.get("label", "").lower() for tag in account.get("tags", [])]
                if payload.ignore_tag.lower() in account_tags:
                    continue
                    
            email_list.append(email)
        
        print(f"\nFinal email list: {email_list}")
        
        return {
            "success": True,
            "message": "Accounts retrieved successfully",
            "data": {
                "organization_id": organization_id,
                "organization_name": organization.instantly_organization_name,
                "total_accounts": len(email_list),
                "accounts": email_list
            }
        }
        
    except UserInstantly.DoesNotExist:
        print("✗ Error: Organization not found")
        return {
            "success": False,
            "message": f"Organization with ID {organization_id} not found or you don't have permission to access it."
        }
    except Exception as e:
        print(f"✗ Error: {str(e)}")
        return {
            "success": False,
            "message": f"Error listing accounts: {str(e)}. Please try again or contact support if the issue persists."
        } 