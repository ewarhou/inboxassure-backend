from typing import List
from ninja import Router
from django.shortcuts import get_object_or_404
from django.http import HttpRequest
from authentication.authorization import AuthBearer
from .models import UserSettings, UserBison, UserInstantly
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
    InstantlyOrganizationDataSchema
)
import requests

router = Router(tags=['Settings'])

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
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        settings.instantly_api_key = payload.instantly_api_key
        settings.instantly_user_token = payload.instantly_user_token
        settings.save()
        
        return 200, {
            "message": "Instantly API key added successfully",
            "data": {
                "instantly_api_key": settings.instantly_api_key,
                "instantly_user_token": settings.instantly_user_token
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.put("/update-instantly-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def update_instantly_api_key(request: HttpRequest, payload: InstantlyApiKeySchema):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.instantly_api_key = payload.instantly_api_key
        settings.instantly_user_token = payload.instantly_user_token
        settings.save()
        
        return 200, {
            "message": "Instantly API key updated successfully",
            "data": {
                "instantly_api_key": settings.instantly_api_key,
                "instantly_user_token": settings.instantly_user_token
            }
        }
    except Exception as e:
        return 400, {"detail": str(e)}

@router.delete("/delete-instantly-api-key", auth=AuthBearer(), response={200: SuccessResponseSchema, 400: ErrorResponseSchema, 404: ErrorResponseSchema})
def delete_instantly_api_key(request: HttpRequest):
    try:
        settings = get_object_or_404(UserSettings, user=request.auth)
        settings.instantly_api_key = None
        settings.instantly_user_token = None
        settings.instantly_status = False  # Reset status when API key is removed
        settings.save()
        
        # Also reset organization statuses
        UserInstantly.objects.filter(user=request.auth).update(instantly_organization_status=False)
        
        return 200, {
            "message": "Instantly API key deleted successfully",
            "data": {}
        }
    except Exception as e:
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
@router.get("/get-emailguard-status", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema})
def get_emailguard_status(request: HttpRequest):
    try:
        try:
            settings = UserSettings.objects.get(user=request.auth)
        except UserSettings.DoesNotExist:
            return 404, {"detail": "User settings not found"}
        
        # Return false if no API key is set
        if not settings.emailguard_api_key:
            return 200, {"status": False, "message": "EmailGuard API key not configured"}
        
        status = settings.emailguard_status or False
        message = "EmailGuard is active" if status else "EmailGuard is not active"
        
        return 200, {"status": status, "message": message}
    except Exception as e:
        print(f"Error in get_emailguard_status: {str(e)}")
        return 400, {"detail": "An unexpected error occurred while checking EmailGuard status"}

@router.post("/check-emailguard-status", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema})
def check_emailguard_status(request: HttpRequest, payload: CheckEmailGuardStatusSchema):
    try:
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        
        # Call EmailGuard API to check status
        headers = {
            'Authorization': f'Bearer {payload.emailguard_api_key}'
        }
        response = requests.get('https://app.emailguard.io/api/v1/workspaces', headers=headers)
        
        # Print API response for debugging
        print("\n=== EmailGuard API Response ===")
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
        print("==============================\n")
        
        # Update status based on API response
        status = response.status_code == 200
        settings.emailguard_status = status
        settings.emailguard_api_key = payload.emailguard_api_key
        settings.save()
        
        message = "EmailGuard is active" if status else "EmailGuard is not active"
        return 200, {"status": status, "message": message}
    except Exception as e:
        return 400, {"detail": str(e)}

# Instantly Status Endpoints
@router.get("/get-instantly-status", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema})
def get_instantly_status(request: HttpRequest):
    try:
        try:
            settings = UserSettings.objects.get(user=request.auth)
        except UserSettings.DoesNotExist:
            return 404, {"detail": "User settings not found"}
        
        # Return false if credentials are missing
        if not settings.instantly_editor_email or not settings.instantly_editor_password:
            return 200, {"status": False, "message": "Instantly credentials not configured"}
        
        status = settings.instantly_status or False
        message = "Instantly is active" if status else "Instantly is not active"
        
        return 200, {"status": status, "message": message}
    except Exception as e:
        print(f"Error in get_instantly_status: {str(e)}")
        return 400, {"detail": "An unexpected error occurred while checking Instantly status"}

@router.post("/check-instantly-status", auth=AuthBearer(), response={200: StatusResponseSchema, 400: ErrorResponseSchema})
def check_instantly_status(request: HttpRequest, payload: CheckInstantlyStatusSchema):
    try:
        settings, created = UserSettings.objects.get_or_create(user=request.auth)
        
        # Login to Instantly
        print("\n=== Instantly API Calls ===")
        login_response = requests.post('https://app.instantly.ai/api/auth/login', json={
            'email': payload.email,
            'password': payload.password
        })
        print("\n1. Login Response:")
        print(f"Status Code: {login_response.status_code}")
        print(f"Response Body: {login_response.text}")
        print(f"Cookies: {dict(login_response.cookies)}")
        
        # Check for invalid credentials
        response_data = login_response.json()
        if response_data.get('error') == 'Invalid credentials':
            settings.instantly_status = False
            settings.save()
            return 200, {"status": False, "message": "Invalid credentials"}
        
        if login_response.status_code != 200 or not login_response.cookies:
            settings.instantly_status = False
            settings.save()
            return 200, {"status": False, "message": "Failed to authenticate with Instantly"}
        
        # Get session token directly from cookies
        session_token = login_response.cookies.get('__session')
        print(f"\n2. Session Token: {session_token}")
        
        if not session_token:
            settings.instantly_status = False
            settings.save()
            return 200, {"status": False, "message": "Failed to get session token"}
        
        # Get user details to store user ID
        headers = {
            'Cookie': f'__session={session_token}',
            'Content-Type': 'application/json'
        }
        user_details_response = requests.get('https://app.instantly.ai/api/user/user_details', headers=headers)
        print("\n2.1 User Details Response:")
        print(f"Status Code: {user_details_response.status_code}")
        print(f"Response Body: {user_details_response.text}")
        
        if user_details_response.status_code == 200:
            user_details = user_details_response.json()
            settings.instantly_user_id = user_details.get('id')
            print(f"\n2.2 Stored User ID: {settings.instantly_user_id}")
        
        # Store the session token
        settings.instantly_user_token = session_token
        settings.instantly_status = True
        settings.save()
        
        # Fetch organizations using session token
        orgs_response = requests.get('https://app.instantly.ai/api/organization/user', headers=headers)
        print("\n3. Organizations Response:")
        print(f"Status Code: {orgs_response.status_code}")
        print(f"Response Body: {orgs_response.text}")
        
        if orgs_response.status_code != 200:
            return 200, {"status": True, "message": "Authenticated but failed to fetch organizations"}
        
        organizations = orgs_response.json()
        
        # Store each organization
        for org in organizations:
            print(f"\n4. Processing Organization: {org['name']}")
            # Get organization token
            auth_response = requests.post(
                'https://app.instantly.ai/api/organization/auth_workspace',
                headers=headers,
                json={'orgID': org['id']}
            )
            print("\n5. Organization Auth Response:")
            print(f"Status Code: {auth_response.status_code}")
            print(f"Response Body: {auth_response.text}")
            
            if auth_response.status_code == 200:
                org_token = auth_response.json().get('org_access')
                print(f"6. Organization Token Received: {org_token is not None}")
                
                # Verify organization access
                verify_headers = {
                    'X-Org-Auth': org_token,
                    'Cookie': f'__session={session_token}',
                    'Content-Type': 'application/json'
                }
                verify_body = {
                    "search": "",
                    "limit": 1,
                    "filter": None,
                    "skip": 0,
                    "include_tags": False
                }
                verify_response = requests.post(
                    'https://app.instantly.ai/backend/api/v1/account/list',
                    headers=verify_headers,
                    json=verify_body
                )
                print("\n7. Organization Verification Response:")
                print(f"Status Code: {verify_response.status_code}")
                print(f"Response Body: {verify_response.text}")
                
                org_status = verify_response.status_code == 200
                
                # Create or update organization
                UserInstantly.objects.update_or_create(
                    user=request.auth,
                    instantly_organization_id=org['id'],
                    defaults={
                        'instantly_organization_name': org['name'],
                        'instantly_organization_token': org_token,
                        'instantly_organization_status': org_status
                    }
                )
                
                # Update overall status based on at least one successful verification
                if org_status:
                    settings.instantly_status = True
                    settings.save()
        
        print("\n=== End of Instantly API Calls ===\n")
        final_status = settings.instantly_status
        return 200, {"status": final_status, "message": "Instantly authenticated and organizations synced successfully" if final_status else "Failed to verify any organization access"}
    except Exception as e:
        print(f"\nError: {str(e)}")
        settings.instantly_status = False
        settings.save()
        return 400, {"detail": str(e)}

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