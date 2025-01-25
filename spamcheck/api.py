from typing import List
from ninja import Router
from django.shortcuts import get_object_or_404
from django.db import transaction, IntegrityError
from django.core.exceptions import ObjectDoesNotExist
from authentication.authorization import AuthBearer
from .schema import CreateSpamcheckSchema, UpdateSpamcheckSchema, LaunchSpamcheckSchema
from .models import UserSpamcheck, UserSpamcheckAccounts, UserSpamcheckCampaignOptions, UserSpamcheckCampaigns
from settings.models import UserInstantly, UserSettings
import requests
from django.conf import settings
from datetime import datetime
from django.utils import timezone

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
                recurring_days=payload.recurring_days
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
    Launch a spamcheck campaign
    
    Steps:
    1. Update spamcheck status to in_progress
    2. Create instantly campaign
    3. Configure campaign options
    4. Get emailguard tag
    5. Add email sequence with emailguard tag
    6. Add leads
    7. Launch campaign
    """
    user = request.auth
    
    try:
        # Get spamcheck and verify ownership with all necessary relations
        spamcheck = UserSpamcheck.objects.select_related(
            'options', 
            'user_organization'
        ).prefetch_related('accounts').get(
            id=payload.spamcheck_id,
            user=user
        )
        
        # Check if status allows launch
        if spamcheck.status != 'pending':
            return {
                "success": False,
                "message": f"Cannot launch spamcheck with status '{spamcheck.status}'. Only pending spamchecks can be launched."
            }
            
        # Get user settings and verify API keys
        user_settings = UserSettings.objects.get(user=user)
        if not user_settings.emailguard_api_key:
            return {
                "success": False,
                "message": "EmailGuard API key not found. Please add your EmailGuard API key in settings."
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
            
        # Start transaction
        with transaction.atomic():
            # 1. Update status to in_progress
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
                    
                    # 2. Create instantly campaign
                    print("\n[1/7] Creating campaign...", flush=True)
                    campaign_name = f"{spamcheck.name} - {account.email_account}"
                    if payload.is_test:
                        campaign_name = f"[TEST] {campaign_name}"
                    
                    request_data = {
                        "name": campaign_name,
                        "user_id": user_settings.instantly_user_id
                    }
                    request_headers = {
                        "Cookie": f"__session={user_settings.instantly_user_token}",
                        "X-Org-Auth": spamcheck.user_organization.instantly_organization_token,
                        "Content-Type": "application/json"
                    }
                    
                    print(f"Request URL: https://app.instantly.ai/backend/api/v1/campaign/create")
                    print(f"Request Headers: {request_headers}")
                    print(f"Request Data: {request_data}")
                        
                    campaign_response = requests.post(
                        "https://app.instantly.ai/backend/api/v1/campaign/create",
                        headers=request_headers,
                        json=request_data
                    )
                    
                    campaign_data = campaign_response.json()
                    campaign_id = campaign_data["id"]
                    print(f"✓ Campaign created with ID: {campaign_id}")
                    
                    # 3. Configure campaign options
                    print("\n[2/7] Configuring campaign options...", flush=True)
                    options_data = {
                        "campaignID": campaign_id,
                        "orgID": spamcheck.user_organization.instantly_organization_id,
                        "emailList": [account.email_account],
                        "openTracking": spamcheck.options.open_tracking,
                        "linkTracking": spamcheck.options.link_tracking,
                        "textOnly": spamcheck.options.text_only,
                        "dailyLimit": "4" if payload.is_test else "50",
                        "emailGap": 300,
                        "stopOnReply": True,
                        "stopOnAutoReply": True
                    }
                    print(f"Options Data: {options_data}")
                    
                    options_response = requests.post(
                        "https://app.instantly.ai/api/campaign/update/options",
                        headers=request_headers,
                        json=options_data
                    )
                    
                    print(f"Options Response Status: {options_response.status_code}")
                    print(f"Options Response: {options_response.text}")
                    
                    options_data = options_response.json()
                    if "error" in options_data:
                        raise Exception(f"Failed to configure campaign: {options_data['error']}")
                    print("✓ Campaign options configured")
                    
                    # 4. Get emailguard tag
                    print("\n[3/7] Getting EmailGuard tag...", flush=True)
                    emailguard_headers = {
                        "Authorization": f"Bearer {user_settings.emailguard_api_key}",
                        "Content-Type": "application/json"
                    }
                    print(f"EmailGuard Headers: {emailguard_headers}")
                    
                    emailguard_data = {
                        "name": campaign_name,
                        "type": "inbox_placement"
                    }
                    print(f"EmailGuard Request Data: {emailguard_data}")
                    
                    try:
                        emailguard_response = requests.post(
                            "https://app.emailguard.io/api/v1/inbox-placement-tests",
                            headers=emailguard_headers,
                            json=emailguard_data,
                            timeout=30  # Add timeout
                        )
                        
                        print(f"EmailGuard Response Status: {emailguard_response.status_code}")
                        print(f"EmailGuard Response: {emailguard_response.text}")
                        
                        if emailguard_response.status_code not in [200, 201]:
                            raise Exception(f"Failed to get EmailGuard tag: Status {emailguard_response.status_code} - {emailguard_response.text}")
                            
                        emailguard_data = emailguard_response.json()
                        if "data" not in emailguard_data or "filter_phrase" not in emailguard_data["data"]:
                            raise Exception(f"EmailGuard response missing filter_phrase: {emailguard_response.text}")
                            
                        emailguard_tag = emailguard_data["data"]["filter_phrase"]
                        print(f"✓ Got EmailGuard tag: {emailguard_tag}")
                        
                    except requests.exceptions.Timeout:
                        raise Exception("EmailGuard API request timed out after 30 seconds")
                    except requests.exceptions.RequestException as e:
                        raise Exception(f"EmailGuard API request failed: {str(e)}")
                    except ValueError as e:
                        raise Exception(f"Invalid JSON response from EmailGuard API: {str(e)}")
                    
                    # 5. Add email sequence with emailguard tag
                    print("\n[4/7] Adding email sequence...", flush=True)
                    sequence_data = {
                        "sequences": [{
                            "steps": [{
                                "type": "email",
                                "variants": [{
                                    "subject": spamcheck.options.subject,
                                    "body": f"{spamcheck.options.body}\n\n{emailguard_tag}"
                                }]
                            }]
                        }],
                        "campaignID": campaign_id,
                        "orgID": spamcheck.user_organization.instantly_organization_id
                    }
                    print(f"Sequence Data: {sequence_data}")
                    
                    sequence_response = requests.post(
                        "https://app.instantly.ai/api/campaign/update/sequences",
                        headers=request_headers,
                        json=sequence_data
                    )
                    
                    print(f"Sequence Response Status: {sequence_response.status_code}")
                    print(f"Sequence Response: {sequence_response.text}")
                    
                    sequence_data = sequence_response.json()
                    if "error" in sequence_data:
                        raise Exception(f"Failed to add sequence: {sequence_data['error']}")
                    print("✓ Email sequence added")
                    
                    # 6. Add leads (email accounts)
                    print("\n[5/7] Adding leads...", flush=True)
                    
                    # Extract test email addresses from EmailGuard response
                    test_emails = [{"email": email["email"]} for email in emailguard_data["data"]["inbox_placement_test_emails"]]
                    
                    leads_data = {
                        "api_key": spamcheck.user_organization.instantly_api_key,
                        "campaign_id": campaign_id,
                        "skip_if_in_workspace": False,
                        "skip_if_in_campaign": False,
                        "leads": test_emails
                    }
                    print(f"Leads Data: {leads_data}")
                    
                    leads_url = "https://api.instantly.ai/api/v1/lead/add"
                    print(f"Leads URL: {leads_url}")
                    
                    leads_response = requests.post(
                        leads_url,
                        headers={"Content-Type": "application/json"},
                        json=leads_data,
                        timeout=30
                    )
                    
                    print(f"Leads Response Status: {leads_response.status_code}")
                    print(f"Leads Response: {leads_response.text}")
                    
                    leads_data = leads_response.json()
                    if "error" in leads_data:
                        raise Exception(f"Failed to add leads: {leads_data['error']}")
                    print(f"✓ Added {len(test_emails)} leads from EmailGuard")
                    
                    # 7. Set campaign schedule
                    print("\n[6/7] Setting campaign schedule...", flush=True)
                    
                    # Always use Detroit timezone and round to nearest 30 minutes
                    detroit_tz = timezone.pytz.timezone('America/Detroit')
                    detroit_time = timezone.localtime(timezone.now(), detroit_tz)
                    minutes = detroit_time.minute
                    
                    if minutes < 30:
                        start_minutes = "30"
                        start_hour = str(detroit_time.hour).zfill(2)
                    else:
                        start_minutes = "00"
                        start_hour = str((detroit_time.hour + 1) % 24).zfill(2)
                    
                    schedule_data = {
                        "api_key": spamcheck.user_organization.instantly_api_key,
                        "campaign_id": campaign_id,
                        "start_date": detroit_time.strftime("%Y-%m-%d"),
                        "end_date": "2029-06-08",
                        "schedules": [
                            {
                                "name": "Everyday",
                                "days": {
                                    "0": True,  # Sunday
                                    "1": True,  # Monday
                                    "2": True,  # Tuesday
                                    "3": True,  # Wednesday
                                    "4": True,  # Thursday
                                    "5": True,  # Friday
                                    "6": True   # Saturday
                                },
                                "timezone": "America/Detroit",
                                "timing": {
                                    "from": f"{start_hour}:{start_minutes}",
                                    "to": f"{start_hour}:30"  # Always 30 minutes after start
                                }
                            }
                        ]
                    }
                    print(f"\nSchedule Request:", flush=True)
                    print(f"- URL: https://api.instantly.ai/api/v1/campaign/set/schedules", flush=True)
                    print(f"- Headers: {{'Content-Type': 'application/json'}}", flush=True)
                    print(f"- Data: {schedule_data}", flush=True)
                    
                    schedule_response = requests.post(
                        "https://api.instantly.ai/api/v1/campaign/set/schedules",
                        headers={"Content-Type": "application/json"},
                        json=schedule_data
                    )
                    
                    print(f"\nSchedule Response:", flush=True)
                    print(f"- Status Code: {schedule_response.status_code}", flush=True)
                    print(f"- Response Text: {schedule_response.text}", flush=True)
                    
                    schedule_data = schedule_response.json()
                    if "error" in schedule_data:
                        raise Exception(f"Failed to set schedule: {schedule_data['error']}")
                    print("✓ Campaign schedule set")
                    
                    # 8. Launch campaign immediately
                    print("\n[7/7] Launching campaign...", flush=True)
                    launch_data = {
                        "api_key": spamcheck.user_organization.instantly_api_key,
                        "campaign_id": campaign_id
                    }
                    print(f"Launch Data: {launch_data}")
                    
                    launch_response = requests.post(
                        "https://api.instantly.ai/api/v1/campaign/launch",
                        headers={"Content-Type": "application/json"},
                        json=launch_data
                    )
                    
                    print(f"Launch Response Status: {launch_response.status_code}")
                    print(f"Launch Response: {launch_response.text}")
                    
                    launch_data = launch_response.json()
                    if "error" in launch_data:
                        raise Exception(f"Failed to launch campaign: {launch_data['error']}")
                    print("✓ Campaign launched")
                    
                    # Store campaign info
                    print("\nStoring campaign info in database...", flush=True)
                    UserSpamcheckCampaigns.objects.create(
                        user=user,
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
                    # If any campaign fails, mark spamcheck as failed
                    spamcheck.status = 'failed'
                    spamcheck.save()
                    raise Exception(f"Failed to setup campaign for {account.email_account}: {str(e)}")
            
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