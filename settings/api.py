from typing import List
from ninja import Router
from django.shortcuts import get_object_or_404
from django.http import HttpRequest
from authentication.authorization import AuthBearer
from .models import UserSettings, UserBison, UserInstantly, UserProfile
from .schema import (
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
    UpdateProfileSchema
)
import requests
import pytz

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
        
        # Store each organization
        for org in organizations:
            print(f"\n4️⃣ Processing Organization: {org['name']}")
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
                
                # Create API key for the organization
                api_key_headers = {
                    'Cookie': f'__session={session_token}',
                    'x-workspace-id': org['id'],
                    'Content-Type': 'application/json'
                }
                
                api_key_data = {
                    'name': 'InboxAssure',
                    'scopes': ['all:all']
                }
                
                print(f"\n5️⃣ Creating API Key for Organization: {org['name']}")
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
                    print(f"✅ API Key created and stored for organization: {org['name']}")
                else:
                    print(f"❌ Failed to create API key for organization: {org['name']}")
                
                org_list.append({
                    "id": instantly_org.id,  # Our database ID
                    "uuid": org['id'],  # Instantly's organization ID
                    "name": org['name']
                })
            else:
                print(f"❌ Failed to get organization token for: {org['name']}")
        
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
        # Set base URL for all users
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        settings.bison_base_url = 'https://app.orbitmailboost.com'
        settings.save()
        
        # Check Bison API connection
        headers = {
            'Authorization': f'Bearer {payload.bison_organization_api_key}'
        }
        response = requests.get(f'{settings.bison_base_url}/api/sender-emails', headers=headers)
        print("\n=== Bison API Response ===")
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        print("==========================\n")
        
        # Create organization with status based on API response
        bison_org = UserBison.objects.create(
            user=request.auth,
            bison_organization_name=payload.bison_organization_name,
            bison_organization_api_key=payload.bison_organization_api_key,
            bison_organization_status=response.status_code == 200
        )
        
        return 200, {
            "message": "Bison organization added successfully",
            "data": {
                "id": bison_org.id,
                "bison_organization_name": bison_org.bison_organization_name,
                "bison_organization_api_key": bison_org.bison_organization_api_key,
                "bison_organization_status": bison_org.bison_organization_status
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.put("/update-bison-organization/{org_id}", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def update_bison_organization(request: HttpRequest, org_id: int, payload: BisonOrganizationSchema):
    try:
        bison_org = get_object_or_404(UserBison, id=org_id, user=request.auth)
        settings = get_object_or_404(UserSettings, user=request.auth)
        
        # Check Bison API connection
        headers = {
            'Authorization': f'Bearer {payload.bison_organization_api_key}'
        }
        response = requests.get(f'{settings.bison_base_url}/api/sender-emails', headers=headers)
        print("\n=== Bison API Response ===")
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        print("==========================\n")
        
        # Update organization with new status
        bison_org.bison_organization_name = payload.bison_organization_name
        bison_org.bison_organization_api_key = payload.bison_organization_api_key
        bison_org.bison_organization_status = response.status_code == 200
        bison_org.save()
        
        return 200, {
            "message": "Bison organization updated successfully",
            "data": {
                "id": bison_org.id,
                "bison_organization_name": bison_org.bison_organization_name,
                "bison_organization_api_key": bison_org.bison_organization_api_key,
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
            settings = UserSettings.objects.get(user=request.auth)
        except UserBison.DoesNotExist:
            return 404, {"detail": f"Bison organization with ID {org_id} not found for this user"}
        except UserSettings.DoesNotExist:
            return 404, {"detail": "User settings not found"}
        
        if not bison_org.bison_organization_api_key:
            bison_org.bison_organization_status = False
            bison_org.save()
            return 200, {"status": False, "message": "Bison organization API key not configured"}
        
        if not settings.bison_base_url:
            return 400, {"detail": "Bison base URL not configured"}
        
        try:
            # Check Bison API connection
            headers = {
                'Authorization': f'Bearer {bison_org.bison_organization_api_key}'
            }
            response = requests.get(f'{settings.bison_base_url}/api/sender-emails', headers=headers)
            print("\n=== Bison API Response ===")
            print(f"Status Code: {response.status_code}")
            print(f"Response Body: {response.text}")
            print("==========================\n")
            
            # Update status based on API response
            status = response.status_code == 200
            bison_org.bison_organization_status = status
            bison_org.save()
            
            if not status and response.status_code == 401:
                message = "Invalid API key or unauthorized access"
            elif not status and response.status_code == 404:
                message = "API endpoint not found"
            elif not status:
                message = f"API check failed with status code: {response.status_code}"
            else:
                message = "Bison organization is active"
                
            return 200, {"status": status, "message": message}
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