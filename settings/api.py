from typing import List
from ninja import Router, Schema
from django.shortcuts import get_object_or_404
from django.http import HttpRequest, FileResponse
from authentication.authorization import AuthBearer
from .models import UserSettings, UserBison, UserInstantly, UserProfile
from settings.schema import (
    InstantlyEditorAccountSchema,
    InstantlyApiKeySchema,
    EmailGuardApiKeySchema,
    BisonOrganizationSchema,
    StatusResponseSchema,
    ErrorResponseSchema,
    SuccessResponseSchema,
    BisonOrganizationResponseSchema,
    InstantlyOrganizationResponseSchema,
    CheckEmailGuardStatusSchema,
    CheckInstantlyStatusSchema,
    InstantlyOrganizationAuthSchema,
    InstantlyOrganizationDataSchema,
    InstantlyStatusResponseSchema,
    InstantlyApiKeyCheckResponseSchema,
    InstantlyEditorAccountResponseSchema,
    EmailGuardKeyResponseSchema,
    BisonKeyResponseSchema,
    UpdateProfileSchema,
    BisonWorkspaceRequestSchema,
    BisonWorkspaceResponseSchema,
    BisonWorkspacesResponseSchema
)
import requests
import pytz
from datetime import datetime
from pathlib import Path

router = Router(tags=['Settings'])
profile_router = Router(tags=['Profile'])

# Instantly Editor Account Endpoints
@router.post("/add-instantly-editor-account", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema})
def add_instantly_editor_account(request: HttpRequest, payload: InstantlyEditorAccountSchema):
    try:
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        settings.instantly_editor_email = payload.instantly_editor_email
        settings.instantly_editor_password = payload.instantly_editor_password
        settings.save()
        
        return 200, {
            "message": "Instantly editor account added successfully",
            "data": {
                "instantly_editor_email": settings.instantly_editor_email,
                "instantly_editor_password": settings.instantly_editor_password
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.put("/update-instantly-editor-account", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def update_instantly_editor_account(request: HttpRequest, payload: InstantlyEditorAccountSchema):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.instantly_editor_email = payload.instantly_editor_email
        settings.instantly_editor_password = payload.instantly_editor_password
        settings.save()
        
        return 200, {
            "message": "Instantly editor account updated successfully",
            "data": {
                "instantly_editor_email": settings.instantly_editor_email,
                "instantly_editor_password": settings.instantly_editor_password
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.delete("/delete-instantly-editor-account", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def delete_instantly_editor_account(request: HttpRequest):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.instantly_editor_email = None
        settings.instantly_editor_password = None
        settings.instantly_status = False  # Reset status when credentials are removed
        settings.save()
        
        # Also reset organization statuses
        UserInstantly.objects.filter(user=request.auth).update(instantly_organization_status=False)
        
        return 200, {
            "message": "Instantly editor account deleted successfully",
            "data": {}
        }
    except Exception as e:
        return 400, {"detail": str(e)}

# Instantly API Key Endpoints
@router.post("/add-instantly-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema})
def add_instantly_api_key(request: HttpRequest, payload: InstantlyApiKeySchema):
    try:
        # Get or create the organization
        instantly_org = get_object_or_404(UserInstantly, user=request.auth, id=payload.organization_id)
        instantly_org.instantly_api_key = payload.instantly_api_key
        instantly_org.save()
        
        return 200, {
            "message": "Instantly API key added successfully",
            "data": {
                "organization_id": instantly_org.instantly_organization_id,
                "organization_name": instantly_org.instantly_organization_name,
                "instantly_api_key": instantly_org.instantly_api_key
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/check-instantly-api-key/{org_id}", auth=AuthBearer(), response={200: InstantlyApiKeyCheckResponseSchema, 400: ErrorResponseSchema})
def check_instantly_api_key(request: HttpRequest, org_id: int):
    try:
        instantly_org = get_object_or_404(UserInstantly, user=request.auth, id=org_id)
        
        if not instantly_org.instantly_api_key:
            print("❌ API key not configured for this organization")
            return 200, {"status": False, "message": "API key not configured for this organization"}
        
        # Check API Key using accounts endpoint
        print("\n🔐 === Instantly API Key Check ===")
        print(f"🏢 Organization: {instantly_org.instantly_organization_name}")
        print(f"🔑 Using API Key: {instantly_org.instantly_api_key}")
        
        headers = {
            'Authorization': f'Bearer {instantly_org.instantly_api_key}',
            'Content-Type': 'application/json'
        }
        
        url = 'https://api.instantly.ai/api/v2/accounts?limit=1'
        print(f"\n📡 Calling: {url}")
        print(f"📤 Request Headers: {headers}")
        
        api_response = requests.get(url, headers=headers)
        print(f"\n📥 Response Status: {api_response.status_code}")
        print(f"📄 Response Body: {api_response.text}")
        
        status = api_response.status_code == 200
        instantly_org.instantly_organization_status = status
        instantly_org.save()
        
        if status:
            print("✅ API key is valid")
            message = "API key is valid"
        else:
            print("❌ API key is invalid")
            if api_response.status_code == 401:
                message = "Invalid API key"
            elif api_response.status_code == 403:
                message = "API key does not have required permissions"
            else:
                message = f"API key check failed with status code: {api_response.status_code}"
        
        return 200, {"status": status, "message": message}
        
    except UserInstantly.DoesNotExist:
        print("❌ Organization not found")
        return 404, {"detail": "Organization not found"}
        
    except requests.RequestException as e:
        print(f"❌ Request failed: {str(e)}")
        instantly_org.instantly_organization_status = False
        instantly_org.save()
        return 400, {"detail": f"Failed to connect to Instantly API: {str(e)}"}
        
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        instantly_org.instantly_organization_status = False
        instantly_org.save()
        return 400, {"detail": str(e)}

@router.get("/check-instantly-status", auth=AuthBearer(), response={200: InstantlyStatusResponseSchema, 400: ErrorResponseSchema})
def check_instantly_status(request: HttpRequest):
    try:
        try:
            settings = UserSettings.objects.get(user=request.auth)
        except UserSettings.DoesNotExist:
            print("❌ User settings not found")
            return 404, {"detail": "User settings not found"}
        
        if not settings.instantly_editor_email or not settings.instantly_editor_password:
            print("❌ Editor account credentials not configured")
            return 200, {
                "status": False,
                "message": "Editor account credentials not configured",
                "user_id": None,
                "organizations": []
            }
        
        # Check Editor Account
        print("\n🔐 === Instantly Editor Account Check ===")
        print(f"📧 Using email: {settings.instantly_editor_email}")
        login_response = requests.post('https://app.instantly.ai/api/auth/login', json={
            'email': settings.instantly_editor_email,
            'password': settings.instantly_editor_password
        })
        print("\n1️⃣ Login Response:")
        print(f"📡 Status Code: {login_response.status_code}")
        print(f"📄 Response Body: {login_response.text}")
        print(f"🍪 Cookies: {dict(login_response.cookies)}")
        
        # Check for invalid credentials
        response_data = login_response.json()
        if response_data.get('error') == 'Invalid credentials':
            print("❌ Invalid credentials")
            settings.instantly_status = False
            settings.save()
            return 200, {
                "status": False,
                "message": "Invalid editor account credentials",
                "user_id": None,
                "organizations": []
            }
        
        if login_response.status_code != 200 or not login_response.cookies:
            print("❌ Failed to authenticate")
            settings.instantly_status = False
            settings.save()
            return 200, {
                "status": False,
                "message": "Failed to authenticate with Instantly",
                "user_id": None,
                "organizations": []
            }
        
        # Get session token
        session_token = login_response.cookies.get('__session')
        if not session_token:
            print("❌ No session token found")
            settings.instantly_status = False
            settings.save()
            return 200, {
                "status": False,
                "message": "Failed to get session token",
                "user_id": None,
                "organizations": []
            }
        
        print("✅ Successfully authenticated")
        
        # Get user details
        headers = {
            'Cookie': f'__session={session_token}',
            'Content-Type': 'application/json'
        }
        print("\n2️⃣ Getting user details...")
        user_details_response = requests.get('https://app.instantly.ai/api/user/user_details', headers=headers)
        print(f"📡 Status Code: {user_details_response.status_code}")
        print(f"📄 Response Body: {user_details_response.text}")
        
        user_id = None
        if user_details_response.status_code == 200:
            user_details = user_details_response.json()
            user_id = user_details.get('id')
            settings.instantly_user_id = user_id
            settings.instantly_user_token = session_token
            settings.save()
            print(f"✅ Stored User ID: {user_id}")
        
        # Fetch organizations
        print("\n3️⃣ Fetching organizations...")
        orgs_response = requests.get('https://app.instantly.ai/api/organization/user', headers=headers)
        print(f"📡 Status Code: {orgs_response.status_code}")
        print(f"📄 Response Body: {orgs_response.text}")
        
        if orgs_response.status_code != 200:
            print("❌ Failed to fetch organizations")
            return 200, {
                "status": True,
                "message": "Authenticated but failed to fetch organizations",
                "user_id": user_id,
                "organizations": []
            }
        
        organizations = orgs_response.json()
        org_list = []
        
        # Keep track of fetched organization IDs for cleanup
        fetched_org_ids = []
        
        # Store each organization
        for org in organizations:
            print(f"\n4️⃣ Processing Organization: {org['name']}")
            fetched_org_ids.append(org['id'])
            # Get organization token
            auth_response = requests.post(
                'https://app.instantly.ai/api/organization/auth_workspace',
                headers=headers,
                json={'orgID': org['id']}
            )
            print(f"📡 Organization Auth Status: {auth_response.status_code}")
            print(f"📄 Organization Auth Response: {auth_response.text}")
            
            if auth_response.status_code == 200:
                org_token = auth_response.json().get('org_access')
                print(f"✅ Got organization token for: {org['name']}")
                
                # Create or update organization
                instantly_org, created = UserInstantly.objects.update_or_create(
                    user=request.auth,
                    instantly_organization_id=org['id'],
                    defaults={
                        'instantly_organization_name': org['name'],
                        'instantly_organization_token': org_token,
                        'instantly_organization_status': True
                    }
                )
                print(f"✅ {'Created' if created else 'Updated'} organization in database: {org['name']}")
                
                # Check existing API keys
                api_key_headers = {
                    'Cookie': f'__session={session_token}',
                    'x-workspace-id': org['id'],
                    'Content-Type': 'application/json'
                }
                
                print(f"\n5️⃣ Checking existing API Keys for Organization: {org['name']}")
                list_keys_response = requests.get(
                    'https://app.instantly.ai/backend/api/v2/api-keys?limit=100',
                    headers=api_key_headers
                )
                print(f"📡 List Keys Response Status: {list_keys_response.status_code}")
                print(f"📄 List Keys Response Body: {list_keys_response.text}")
                
                should_create_key = True  # Flag to control key creation
                if list_keys_response.status_code == 200:
                    keys_data = list_keys_response.json()
                    print(f"\n🔍 Found {len(keys_data.get('items', []))} API keys")
                    print(f"🗄️ Current API key in database: {instantly_org.instantly_api_key}")
                    
                    # Check if any InboxAssure key exists
                    inboxassure_keys = [key for key in keys_data.get('items', []) if key.get('name') == 'InboxAssure']
                    if inboxassure_keys:
                        print(f"\n✅ Found {len(inboxassure_keys)} existing InboxAssure API key(s)")
                        should_create_key = False  # Don't create a new key if any exist
                        
                        # Try to find a non-empty key
                        for key in inboxassure_keys:
                            existing_api_key = key.get('key')
                            print(f"\n📑 Checking key: {key.get('name')}")
                            
                            if existing_api_key:
                                print(f"🔑 Found API key: {existing_api_key[:10]}...{existing_api_key[-10:]}")  # Show first and last 10 chars
                                
                                if existing_api_key != instantly_org.instantly_api_key:
                                    print(f"⚠️ API key in database differs from found key")
                                    print(f"📥 Updating database with found key")
                                    instantly_org.instantly_api_key = existing_api_key
                                    instantly_org.save()
                                    print(f"✅ Database updated with found key")
                                else:
                                    print(f"✅ API key in database matches found key")
                                break
                            else:
                                print(f"⚠️ Found key is empty")
                    else:
                        print(f"\n⚠️ No InboxAssure API keys found for organization: {org['name']}")
                        should_create_key = True
                
                if should_create_key:  # Only create new key if flag is True
                    # Create new API key if none exists
                    api_key_data = {
                        'name': 'InboxAssure',
                        'scopes': ['all:all']
                    }
                    
                    print(f"\n6️⃣ Creating new API Key for Organization: {org['name']}")
                    print(f"📡 Request Headers: {api_key_headers}")
                    print(f"📄 Request Body: {api_key_data}")
                    
                    api_key_response = requests.post(
                        'https://app.instantly.ai/backend/api/v2/api-keys',
                        headers=api_key_headers,
                        json=api_key_data
                    )
                    print(f"📡 API Key Response Status: {api_key_response.status_code}")
                    print(f"📄 API Key Response Body: {api_key_response.text}")
                    
                    if api_key_response.status_code == 200:
                        api_key_data = api_key_response.json()
                        instantly_org.instantly_api_key = api_key_data.get('key')
                        instantly_org.save()
                        print(f"✅ New API Key created and stored for organization: {org['name']}")
                    else:
                        print(f"❌ Failed to create API key for organization: {org['name']}")
                
                org_list.append({
                    "id": instantly_org.id,  # Our database ID
                    "uuid": org['id'],  # Instantly's organization ID
                    "name": org['name']
                })
            else:
                print(f"❌ Failed to get organization token for: {org['name']}")
        
        # Delete organizations that weren't returned by the API
        UserInstantly.objects.filter(
            user=request.auth
        ).exclude(
            instantly_organization_id__in=fetched_org_ids
        ).delete()
        print(f"\n🧹 Cleaned up organizations not present in Instantly.ai")

        settings.instantly_status = True
        settings.save()
        
        print(f"\n✅ Successfully processed {len(org_list)} organizations")
        return 200, {
            "status": True,
            "message": f"Successfully fetched {len(org_list)} organizations",
            "user_id": user_id,
            "organizations": org_list
        }
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        settings.instantly_status = False
        settings.save()
        return 400, {"detail": str(e)}

# EmailGuard API Key Endpoints
@router.post("/add-emailguard-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema})
def add_emailguard_api_key(request: HttpRequest, payload: EmailGuardApiKeySchema):
    try:
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        settings.emailguard_api_key = payload.emailguard_api_key
        settings.save()
        
        return 200, {
            "message": "EmailGuard API key added successfully",
            "data": {
                "emailguard_api_key": settings.emailguard_api_key
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.put("/update-emailguard-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def update_emailguard_api_key(request: HttpRequest, payload: EmailGuardApiKeySchema):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.emailguard_api_key = payload.emailguard_api_key
        settings.save()
        
        return 200, {
            "message": "EmailGuard API key updated successfully",
            "data": {
                "emailguard_api_key": settings.emailguard_api_key
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.delete("/delete-emailguard-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def delete_emailguard_api_key(request: HttpRequest):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.emailguard_api_key = None
        settings.emailguard_status = False  # Reset status when API key is removed
        settings.save()
        
        return 200, {
            "message": "EmailGuard API key deleted successfully",
            "data": {}
        }
    except Exception as e:
        return 400, {"detail": str(e)}

# EmailGuard Status Endpoints
@router.get("/check-emailguard-status", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema, 500: ErrorResponseSchema})
def get_emailguard_status(request: HttpRequest):
    """
    Check EmailGuard status by verifying the API key
    """
    try:
        settings = UserSettings.objects.get(user=request.auth)
        
        if not settings.emailguard_api_key:
            print("❌ EmailGuard API key not found")
            return 404, {"detail": "EmailGuard API key not configured"}
            
        # Call EmailGuard API to verify the key
        headers = {
            'Authorization': f'Bearer {settings.emailguard_api_key}',
            'Content-Type': 'application/json'
        }
        
        print("\n🔍 Checking EmailGuard status...")
        print(f"📡 Calling: https://app.emailguard.io/api/v1/user")
        print(f"🔑 Using API key: {settings.emailguard_api_key}")
        
        response = requests.get(
            'https://app.emailguard.io/api/v1/user',
            headers=headers
        )
        
        print(f"\n📥 Response Status: {response.status_code}")
        print(f"📄 Response Body: {response.text}")
        
        if response.status_code == 200:
            settings.emailguard_status = True
            settings.save()
            return 200, {
                "status": True,
                "message": "EmailGuard connection verified successfully",
                "data": response.json()
            }
        else:
            settings.emailguard_status = False
            settings.save()
            return 400, {"detail": "Failed to verify EmailGuard connection"}
            
    except UserSettings.DoesNotExist:
        print("❌ User settings not found")
        return 404, {"detail": "User settings not found"}
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Request failed: {str(e)}")
        settings.emailguard_status = False
        settings.save()
        return 500, {"detail": f"Failed to connect to EmailGuard API: {str(e)}"}
        
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        settings.emailguard_status = False
        settings.save()
        return 500, {"detail": f"An unexpected error occurred: {str(e)}"}

# Bison Organization Endpoints
@router.post("/add-bison-organization", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema})
def add_bison_organization(request: HttpRequest, payload: BisonOrganizationSchema):
    try:
        # Create organization with initial status as False
        bison_org = UserBison.objects.create(
            user=request.auth,
            bison_organization_name=payload.bison_organization_name,
            bison_organization_api_key=payload.bison_organization_api_key,
            base_url=payload.base_url,
            bison_organization_status=False  # Set initial status to False
        )
        
        return 200, {
            "message": "Bison organization added successfully",
            "data": {
                "id": bison_org.id,
                "bison_organization_name": bison_org.bison_organization_name,
                "bison_organization_api_key": bison_org.bison_organization_api_key,
                "base_url": bison_org.base_url,
                "bison_organization_status": bison_org.bison_organization_status
            }
        }
    except Exception as e:
        error_message = str(e)
        if "Duplicate entry" in error_message and "user_bison_user_id_bison_organization_name" in error_message:
            # Extract organization name from the error message
            org_name = payload.bison_organization_name
            return 400, {"detail": f"An organization with the name '{org_name}' already exists for your account. Please use a different name."}
        return 400, {"detail": str(e)}

@router.put("/update-bison-organization/{org_id}", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def update_bison_organization(request: HttpRequest, org_id: int, payload: BisonOrganizationSchema):
    try:
        bison_org = get_object_or_404(UserBison, id=org_id, user=request.auth)
        
        # Check Bison API connection
        headers = {
            'Authorization': f'Bearer {payload.bison_organization_api_key}'
        }
        response = requests.get(f'{payload.base_url}/api/sender-emails', headers=headers)
        print("\n=== Bison API Response ===")
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        print("==========================\n")
        
        # Update organization with new status
        bison_org.bison_organization_name = payload.bison_organization_name
        bison_org.bison_organization_api_key = payload.bison_organization_api_key
        bison_org.base_url = payload.base_url
        bison_org.bison_organization_status = response.status_code == 200
        bison_org.save()
        
        return 200, {
            "message": "Bison organization updated successfully",
            "data": {
                "id": bison_org.id,
                "bison_organization_name": bison_org.bison_organization_name,
                "bison_organization_api_key": bison_org.bison_organization_api_key,
                "base_url": bison_org.base_url,
                "bison_organization_status": bison_org.bison_organization_status
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.delete("/delete-bison-organization/{org_id}", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def delete_bison_organization(request: HttpRequest, org_id: int):
    try:
        bison_org = get_object_or_404(UserBison, id=org_id, user=request.auth)
        bison_org.delete()
        
        return 200, {
            "message": "Bison organization deleted successfully",
            "data": {}
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/get-bison-organization-status/{org_id}", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def get_bison_organization_status(request: HttpRequest, org_id: int):
    try:
        # First check if org_id is valid
        if not isinstance(org_id, int):
            return 400, {"detail": "Invalid organization ID format"}
            
        try:
            bison_org = UserBison.objects.get(id=org_id, user=request.auth)
        except UserBison.DoesNotExist:
            return 404, {"detail": f"Bison organization with ID {org_id} not found for this user"}
        
        # Return false if API key is missing
        if not bison_org.bison_organization_api_key:
            return 200, {"status": False, "message": "Bison organization API key not configured"}
        
        status = bison_org.bison_organization_status or False
        message = "Bison organization is active" if status else "Bison organization is not active"
        
        return 200, {"status": status, "message": message}
    except ValueError as e:
        return 400, {"detail": f"Invalid input: {str(e)}"}
    except Exception as e:
        print(f"Error in get_bison_organization_status: {str(e)}")
        return 400, {"detail": "An unexpected error occurred while checking organization status"}

@router.post("/check-bison-organization-status/{org_id}", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def check_bison_organization_status(request: HttpRequest, org_id: int):
    try:
        # First check if org_id is valid
        if not isinstance(org_id, int):
            return 400, {"detail": "Invalid organization ID format"}
            
        try:
            bison_org = UserBison.objects.get(id=org_id, user=request.auth)
        except UserBison.DoesNotExist:
            return 404, {"detail": f"Bison organization with ID {org_id} not found for this user"}
        
        if not bison_org.bison_organization_api_key:
            bison_org.bison_organization_status = False
            bison_org.save()
            return 200, {"status": False, "message": "Bison organization API key not configured"}
        
        try:
            # Check Bison API connection using users endpoint
            headers = {
                'Authorization': f'Bearer {bison_org.bison_organization_api_key}'
            }
            
            # First validate base_url format and accessibility
            try:
                response = requests.get(f'{bison_org.base_url}/api/users', headers=headers)
                print("\n=== Bison API Response ===")
                print(f"Status Code: {response.status_code}")
                print(f"Response Body: {response.text}")
                print("==========================\n")
                
                # Update status based on response status code only
                status = response.status_code == 200
                message = "Bison organization is active" if status else "Invalid API key or unauthorized access"
                
                bison_org.bison_organization_status = status
                bison_org.save()
                
                if not status:
                    if response.status_code == 404:
                        message = f"API endpoint not found at {bison_org.base_url}/api/users"
                    elif response.status_code == 401 or response.status_code == 403:
                        message = "Invalid API key or unauthorized access"
                    else:
                        message = f"API returned error status code: {response.status_code}"
                    
                return 200, {"status": status, "message": message}
                
            except requests.exceptions.ConnectionError as e:
                bison_org.bison_organization_status = False
                bison_org.save()
                if "Name or service not known" in str(e) or "Failed to resolve" in str(e):
                    return 200, {"status": False, "message": "Base URL is not working. Please check if the URL is correct"}
                return 200, {"status": False, "message": "Base URL is not working"}
            except requests.exceptions.Timeout:
                bison_org.bison_organization_status = False
                bison_org.save()
                return 200, {"status": False, "message": "Base URL is not working (Connection timeout)"}
            except requests.exceptions.RequestException as e:
                bison_org.bison_organization_status = False
                bison_org.save()
                return 200, {"status": False, "message": "Base URL is not working"}
            
        except requests.RequestException as e:
            bison_org.bison_organization_status = False
            bison_org.save()
            return 400, {"detail": f"Failed to connect to Bison API: {str(e)}"}
            
    except ValueError as e:
        return 400, {"detail": f"Invalid input: {str(e)}"}
    except Exception as e:
        print(f"Error in check_bison_organization_status: {str(e)}")
        return 400, {"detail": "An unexpected error occurred while checking organization status"}

# List Endpoints
@router.get("/list-bison-organizations", auth=AuthBearer(), response={200: List[BisonOrganizationResponseSchema], 400: ErrorResponseSchema})
def list_bison_organizations(request: HttpRequest):
    try:
        bison_orgs = UserBison.objects.filter(user=request.auth)
        return 200, list(bison_orgs)
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/list-instantly-organizations", auth=AuthBearer(), response={200: List[InstantlyOrganizationResponseSchema], 400: ErrorResponseSchema})
def list_instantly_organizations(request: HttpRequest):
    try:
        instantly_orgs = UserInstantly.objects.filter(user=request.auth)
        return 200, list(instantly_orgs)
    except Exception as e:
        return 400, {"detail": str(e)}

@profile_router.put("/update", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema})
def update_profile(request, payload: UpdateProfileSchema):
    """Update user's profile settings"""
    try:
        # Get or create profile
        profile, created = UserProfile.objects.get_or_create(user=request.auth)
        
        # Update timezone if provided
        if payload.timezone is not None:
            try:
                pytz.timezone(payload.timezone)
                profile.timezone = payload.timezone
            except pytz.exceptions.UnknownTimeZoneError:
                return 400, {"detail": f"Invalid timezone: {payload.timezone}"}
        
        profile.save()
        
        return 200, {
            "message": "Profile updated successfully",
            "data": {
                "timezone": profile.timezone
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/instantly-editor", auth=AuthBearer(), response={200: InstantlyEditorAccountResponseSchema, 400: ErrorResponseSchema})
def get_instantly_editor_account(request):
    """Get user's Instantly editor account credentials"""
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        return 200, {
            "instantly_editor_email": settings.instantly_editor_email,
            "instantly_editor_password": settings.instantly_editor_password,
            "instantly_status": settings.instantly_status
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/emailguard-key", auth=AuthBearer(), response={200: EmailGuardKeyResponseSchema, 400: ErrorResponseSchema})
def get_emailguard_key(request):
    """Get user's EmailGuard API key"""
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        return 200, {
            "emailguard_api_key": settings.emailguard_api_key,
            "emailguard_status": settings.emailguard_status
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/bison-organizations", auth=AuthBearer(), response={200: List[BisonKeyResponseSchema], 400: ErrorResponseSchema})
def get_bison_organizations(request):
    """Get user's Bison organizations and their API keys"""
    try:
        bison_orgs = UserBison.objects.filter(user=request.auth)
        return 200, [{
            "bison_organization_name": org.bison_organization_name,
            "bison_organization_api_key": org.bison_organization_api_key,
            "bison_organization_status": org.bison_organization_status
        } for org in bison_orgs]
    except Exception as e:
        return 400, {"detail": str(e)}

@router.get("/download-logs", auth=AuthBearer())
def download_logs(request):
    """Download the terminal logs file"""
    try:
        logs_path = Path("terminal_logs.txt")
        if logs_path.exists():
            response = FileResponse(
                open(logs_path, 'rb'),
                content_type='text/plain'
            )
            response['Content-Disposition'] = f'attachment; filename="terminal_logs_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt"'
            return response
        return {"message": "Logs file not found"}, 404
    except Exception as e:
        return {"message": str(e)}, 500

def log_to_terminal(module: str, action: str, message: str):
    """Utility function to write logs to the terminal_logs.txt file"""
    try:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_entry = f"[{timestamp}] [{module}] [{action}] {message}\n"
        
        with open("terminal_logs.txt", "a") as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Error writing to logs: {str(e)}")

@router.get("/list-bison-workspaces", auth=AuthBearer())
def list_bison_workspaces(request):
    """
    List all connected Bison workspaces for the authenticated user
    """
    user = request.auth
    
    try:
        # Get all active Bison organizations for the user
        bison_organizations = UserBison.objects.filter(
            user=user,
            bison_organization_status=True
        ).values(
            'id',
            'bison_organization_id',
            'bison_organization_name',
            'bison_organization_status',
            'created_at',
            'updated_at'
        )
        
        if not bison_organizations:
            return {
                "success": False,
                "message": "No active Bison workspaces found. Please connect at least one workspace first.",
                "data": []
            }
        
        return {
            "success": True,
            "message": f"Successfully retrieved {len(bison_organizations)} Bison workspaces",
            "data": list(bison_organizations)
        }
        
    except Exception as e:
        log_to_terminal("Settings", "List Bison Workspaces", f"Error: {str(e)}")
        return {
            "success": False,
            "message": f"Error retrieving Bison workspaces: {str(e)}",
            "data": []
        }

@router.post("/retrieve-bison-workspaces", auth=AuthBearer(), response={200: BisonWorkspacesResponseSchema, 400: ErrorResponseSchema})
def retrieve_bison_workspaces(request: HttpRequest, payload: BisonWorkspaceRequestSchema):
    """
    Retrieve workspaces from Bison, create API keys for each, add them to our database,
    verify connections, and return only connected workspaces.
    """
    try:
        print("\n🚀 === STARTING BISON WORKSPACE RETRIEVAL ===")
        print(f"👤 User: {request.auth.username}")
        print(f"🌐 Base URL: {payload.base_url}")
        print(f"🔑 Admin API Key: {payload.admin_api_key[:10]}...{payload.admin_api_key[-5:] if len(payload.admin_api_key) > 15 else ''}")
        
        log_to_terminal("Bison", "Workspaces", f"User {request.auth.username} started retrieving Bison workspaces")
        base_url = payload.base_url
        admin_api_key = payload.admin_api_key
        
        # Step 1: Fetch workspaces from Bison
        headers = {
            'Authorization': f'Bearer {admin_api_key}'
        }
        
        print("\n1️⃣ FETCHING WORKSPACES FROM BISON")
        print(f"📡 Calling: {base_url}/api/workspaces")
        print(f"🔑 Using Admin API Key: {admin_api_key[:10]}...{admin_api_key[-5:] if len(admin_api_key) > 15 else ''}")
        
        try:
            workspaces_response = requests.get(f'{base_url}/api/workspaces', headers=headers)
            print(f"\n📥 Workspaces Response Status: {workspaces_response.status_code}")
            print(f"📄 Workspaces Response Body: {workspaces_response.text[:1000]}...")
            
            log_to_terminal("Bison", "Workspaces", f"Workspaces API response: {workspaces_response.status_code}")
            
            if workspaces_response.status_code != 200:
                print(f"❌ Failed to fetch workspaces: {workspaces_response.text}")
                return 400, {"detail": f"Failed to fetch workspaces: {workspaces_response.text}"}
            
            workspaces_data = workspaces_response.json()
            
            if 'data' not in workspaces_data:
                print("❌ Invalid response format: 'data' key not found in response")
                return 400, {"detail": "Invalid response format from Bison API"}
            
            workspaces = workspaces_data['data']
            print(f"\n✅ Found {len(workspaces)} workspaces")
            for idx, workspace in enumerate(workspaces):
                print(f"  {idx+1}. {workspace.get('name', 'Unknown')} (ID: {workspace.get('id', 'Unknown')})")
            
            log_to_terminal("Bison", "Workspaces", f"Found {len(workspaces)} workspaces")
            
            # Step 2: Process each workspace
            connected_workspaces = []
            
            for workspace in workspaces:
                workspace_id = workspace.get('id')
                workspace_name = workspace.get('name')
                
                print(f"\n2️⃣ PROCESSING WORKSPACE: {workspace_name} (ID: {workspace_id})")
                
                if not workspace_id or not workspace_name:
                    print(f"⚠️ Skipping workspace with missing ID or name")
                    log_to_terminal("Bison", "Workspaces", f"Skipping workspace with missing ID or name")
                    continue
                
                log_to_terminal("Bison", "Workspaces", f"Processing workspace: {workspace_name} (ID: {workspace_id})")
                
                # Step 3: Create API key for the workspace
                try:
                    print(f"\n3️⃣ CREATING API KEY FOR WORKSPACE: {workspace_name}")
                    print(f"📡 Calling: {base_url}/api/workspaces/v1.1/{workspace_id}/api-tokens")
                    print(f"📤 Request Body: {{'name': '{workspace_name}'}}")
                    
                    api_token_response = requests.post(
                        f'{base_url}/api/workspaces/v1.1/{workspace_id}/api-tokens',
                        headers={
                            'Content-Type': 'application/json',
                            'Authorization': f'Bearer {admin_api_key}'
                        },
                        json={
                            'name': workspace_name
                        }
                    )
                    
                    print(f"\n📥 API Token Response Status: {api_token_response.status_code}")
                    print(f"📄 API Token Response Body: {api_token_response.text}")
                    
                    if api_token_response.status_code != 200 and api_token_response.status_code != 201:
                        print(f"❌ Failed to create API token: {api_token_response.text}")
                        log_to_terminal("Bison", "Workspaces", f"Failed to create API token for workspace {workspace_name}: {api_token_response.text}")
                        continue
                    
                    api_token_data = api_token_response.json()
                    
                    # Check if the response has data.plain_text_token structure
                    if 'data' in api_token_data and 'plain_text_token' in api_token_data['data']:
                        api_key = api_token_data['data']['plain_text_token']
                        print(f"✅ Found API token in data.plain_text_token: {api_key[:10]}...{api_key[-5:] if len(api_key) > 15 else ''}")
                    # Fallback to the old structure
                    elif 'token' in api_token_data:
                        api_key = api_token_data.get('token')
                        print(f"✅ Found API token in token field: {api_key[:10]}...{api_key[-5:] if len(api_key) > 15 else ''}")
                    else:
                        print("❌ No API token found in response")
                        print(f"📄 Response structure: {list(api_token_data.keys())}")
                        if 'data' in api_token_data:
                            print(f"📄 Data structure: {list(api_token_data['data'].keys())}")
                        log_to_terminal("Bison", "Workspaces", f"No API token returned for workspace {workspace_name}")
                        continue
                    
                    if not api_key:
                        print("❌ API token is empty")
                        log_to_terminal("Bison", "Workspaces", f"Empty API token returned for workspace {workspace_name}")
                        continue
                    
                    print(f"✅ Created API token: {api_key[:10]}...{api_key[-5:] if len(api_key) > 15 else ''}")
                    log_to_terminal("Bison", "Workspaces", f"Created API token for workspace {workspace_name}")
                    
                    # Step 4: Add workspace to our database
                    print(f"\n4️⃣ ADDING WORKSPACE TO DATABASE: {workspace_name}")
                    
                    bison_org_payload = BisonOrganizationSchema(
                        bison_organization_name=workspace_name,
                        bison_organization_api_key=api_key,
                        base_url=base_url
                    )
                    
                    add_response = add_bison_organization(request, bison_org_payload)
                    print(f"\n📥 Add Organization Response Status: {add_response[0]}")
                    print(f"📄 Add Organization Response: {add_response[1]}")
                    
                    if add_response[0] != 200:
                        print(f"❌ Failed to add workspace to database: {add_response[1]}")
                        log_to_terminal("Bison", "Workspaces", f"Failed to add workspace {workspace_name} to database: {add_response[1]}")
                        continue
                    
                    org_id = add_response[1]['data']['id']
                    print(f"✅ Added workspace to database with ID: {org_id}")
                    log_to_terminal("Bison", "Workspaces", f"Added workspace {workspace_name} to database with ID {org_id}")
                    
                    # Step 5: Verify connection
                    print(f"\n5️⃣ VERIFYING CONNECTION FOR WORKSPACE: {workspace_name}")
                    
                    check_response = check_bison_organization_status(request, org_id)
                    print(f"\n📥 Check Status Response Status: {check_response[0]}")
                    print(f"📄 Check Status Response: {check_response[1]}")
                    
                    if check_response[0] != 200:
                        print(f"❌ Failed to check workspace status: {check_response[1]}")
                        log_to_terminal("Bison", "Workspaces", f"Failed to check workspace {workspace_name} status: {check_response[1]}")
                        continue
                    
                    status = check_response[1]['status']
                    print(f"🔍 Workspace connection status: {status}")
                    log_to_terminal("Bison", "Workspaces", f"Workspace {workspace_name} connection status: {status}")
                    
                    if not status:
                        print(f"❌ Workspace connection failed, deleting from database")
                        # Delete unconnected workspace
                        try:
                            bison_org = UserBison.objects.get(id=org_id, user=request.auth)
                            bison_org.delete()
                            print(f"✅ Deleted unconnected workspace from database")
                            log_to_terminal("Bison", "Workspaces", f"Deleted unconnected workspace {workspace_name}")
                        except UserBison.DoesNotExist:
                            print(f"⚠️ Workspace not found for deletion")
                            log_to_terminal("Bison", "Workspaces", f"Workspace {workspace_name} not found for deletion")
                        continue
                    
                    # Add connected workspace to result
                    print(f"✅ Workspace successfully connected")
                    connected_workspaces.append({
                        "id": workspace_id,
                        "name": workspace_name,
                        "organization_id": org_id,
                        "api_key": api_key,
                        "status": status
                    })
                    
                except Exception as e:
                    print(f"❌ Error processing workspace: {str(e)}")
                    log_to_terminal("Bison", "Workspaces", f"Error processing workspace {workspace_name}: {str(e)}")
                    continue
            
            print(f"\n🏁 FINISHED PROCESSING WORKSPACES")
            print(f"✅ Successfully connected {len(connected_workspaces)} workspaces")
            for idx, workspace in enumerate(connected_workspaces):
                print(f"  {idx+1}. {workspace['name']} (ID: {workspace['id']}, Org ID: {workspace['organization_id']})")
            
            return 200, {
                "workspaces": connected_workspaces,
                "message": f"Successfully retrieved and connected {len(connected_workspaces)} workspaces"
            }
            
        except requests.RequestException as e:
            print(f"❌ Request error: {str(e)}")
            log_to_terminal("Bison", "Workspaces", f"Request error: {str(e)}")
            return 400, {"detail": f"Failed to connect to Bison API: {str(e)}"}
            
    except Exception as e:
        print(f"❌ Unexpected error: {str(e)}")
        log_to_terminal("Bison", "Workspaces", f"Unexpected error: {str(e)}")
        return 400, {"detail": str(e)} 