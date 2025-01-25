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
                    
                    # 1. Get emailguard tag
                    print("\n[1/3] Getting EmailGuard tag...", flush=True)
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
                    if "data" not in emailguard_data or "filter_phrase" not in emailguard_data["data"]:
                        raise Exception(f"EmailGuard response missing filter_phrase: {emailguard_data}")
                    
                    emailguard_tag = emailguard_data["data"]["filter_phrase"]
                    test_emails = emailguard_data["data"]["inbox_placement_test_emails"]
                    print(f"✓ Got EmailGuard tag: {emailguard_tag}")
                    print(f"✓ Got {len(test_emails)} test email addresses")

                    # 2. Create campaign with all settings
                    print("\n[2/3] Creating campaign with settings...", flush=True)
                    print("Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/campaigns")
                    
                    request_headers = {
                        "Authorization": f"Bearer {spamcheck.user_organization.instantly_api_key}",
                        "Content-Type": "application/json"
                    }
                    
                    request_data = {
                        "name": campaign_name,
                        "campaign_schedule": {
                            "schedules": [
                                {
                                    "name": "Default Schedule",
                                    "timing": {
                                        "from": "09:00",
                                        "to": "17:00"
                                    },
                                    "days": {},
                                    "timezone": "UTC"
                                }
                            ]
                        },
                        "email_gap": 1,
                        "text_only": spamcheck.options.text_only,
                        "email_list": [account.email_account],
                        "daily_limit": 4 if payload.is_test else 50,
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
                    campaign_id = campaign_data["id"]
                    print(f"✓ Campaign created with ID: {campaign_id}")
                    
                    # 3. Add leads
                    print("\n[3/3] Adding leads...", flush=True)
                    print("Calling Instantly API endpoint: POST https://api.instantly.ai/api/v2/leads")
                    
                    success_count = 0
                    for test_email in test_emails:
                        try:
                            leads_data = {
                                "campaign": campaign_id,
                                "email": test_email["email"]
                            }
                            print(f"Adding lead: {leads_data}")
                            
                            leads_response = requests.post(
                                "https://api.instantly.ai/api/v2/leads",
                                headers=request_headers,
                                json=leads_data,
                                timeout=30
                            )
                            
                            if leads_response.status_code == 200:
                                success_count += 1
                                print(f"✓ Added lead: {test_email['email']}")
                            else:
                                print(f"Warning: Failed to add lead {test_email['email']}: {leads_response.text}")
                        except Exception as e:
                            print(f"Error adding lead {test_email['email']}: {str(e)}")
                    
                    print(f"✓ Successfully added {success_count} out of {len(test_emails)} leads")
                    
                    if success_count == 0:
                        raise Exception("Failed to add any leads to the campaign")
                    
                    # Store campaign info
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
                    
                    return True
                    
                except Exception as e:
                    print(f"Error processing account {account.email_account}: {str(e)}")
                    return False
            
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