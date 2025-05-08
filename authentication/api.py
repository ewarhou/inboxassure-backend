from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.hashers import make_password
from ninja import Router, Schema, File, Form, UploadedFile
from ninja.security import HttpBearer
from ninja.files import UploadedFile
import jwt
from datetime import datetime, timedelta
from django.conf import settings
from django.db import connections
from ninja.responses import Response
import logging
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from typing import Dict, Optional, List
from .models import PasswordResetToken, AuthProfile, profile_picture_path
from .schema import (
    TokenSchema, ErrorMessage, PasswordResetRequestSchema, 
    PasswordResetVerifySchema, PasswordResetConfirmSchema, 
    ChangePasswordSchema, UpdateProfileSchema, ProfileResponseSchema,
    AdminPasswordResetSchema, UserListItemSchema, AdminToggleSchema
)
from settings.api import log_to_terminal

logger = logging.getLogger(__name__)

User = get_user_model()
router = Router(tags=["Authentication"])
profile_router = Router(tags=["Profile"])

class SignUpSchema(Schema):
    username: str
    email: str
    password: str
    first_name: str = None
    last_name: str = None

class LoginSchema(Schema):
    email: str
    password: str

class TokenSchema(Schema):
    access_token: str
    token_type: str

class ErrorSchema(Schema):
    detail: str

class ProfilePictureResponse(Schema):
    success: bool
    message: str
    data: Optional[dict] = None

def verify_client_email(email):
    try:
        with connections['default'].cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) FROM inboxassure_clients WHERE client_email = %s",
                [email]
            )
            count = cursor.fetchone()[0]
            logger.info(f"Found {count} clients with email {email}")
            return count > 0
    except Exception as e:
        logger.error(f"Error verifying client email: {str(e)}")
        return False

@router.post("/register", response={200: TokenSchema, 400: ErrorSchema})
def register(request, data: SignUpSchema):
    logger.info(f"Registration attempt for email: {data.email}")
    
    # First verify if the email exists in inboxassure_clients
    if not verify_client_email(data.email):
        logger.warning(f"Email not found in clients database: {data.email}")
        return 400, {"detail": "Email not found in our client database. Please contact support."}
    
    # Check if email already exists in auth users
    if User.objects.filter(email=data.email).exists():
        logger.warning(f"Email already registered in auth users: {data.email}")
        return 400, {"detail": "Email already registered"}
    
    # Check username uniqueness
    if User.objects.filter(username=data.username).exists():
        logger.warning(f"Username already exists: {data.username}")
        return 400, {"detail": "Username already registered"}
    
    try:
        user = User.objects.create(
            username=data.username,
            email=data.email,
            password=make_password(data.password),
            first_name=data.first_name or "",
            last_name=data.last_name or ""
        )
        logger.info(f"Successfully created user: {user.username}")
        
        token = generate_token(user)
        return 200, {"access_token": token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Error creating user: {str(e)}")
        return 400, {"detail": "Error creating user account"}

@router.post("/login", response={200: TokenSchema, 401: ErrorSchema})
def login(request, data: LoginSchema):
    try:
        # Get user by email - handle potential duplicates by getting the most recent one
        users = User.objects.filter(email=data.email).order_by('-date_joined')
        
        if not users.exists():
            logger.warning(f"Login attempt with non-existent email: {data.email}")
            return 401, {"detail": "Invalid credentials"}
            
        # Use the most recently created account with this email
        user = users.first()
        
        # Authenticate using username and password
        authenticated_user = authenticate(username=user.username, password=data.password)
        
        if not authenticated_user:
            logger.warning(f"Failed login attempt for email: {data.email}")
            return 401, {"detail": "Invalid credentials"}
        
        logger.info(f"Successful login for email: {data.email}")
        token = generate_token(authenticated_user)
        return 200, {"access_token": token, "token_type": "bearer"}
    except Exception as e:
        logger.error(f"Error during login: {str(e)}")
        return 401, {"detail": "Login failed"}

def generate_token(user):
    payload = {
        'user_id': user.id,
        'username': user.username,
        'exp': datetime.utcnow() + timedelta(days=1),
        'iat': datetime.utcnow()
    }
    
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

class AuthBearer(HttpBearer):
    def authenticate(self, request, token):
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            return user
        except:
            return None

auth = AuthBearer()

@router.post("/password-reset-request", response={200: Dict, 404: ErrorMessage})
def request_password_reset(request, data: PasswordResetRequestSchema):
    """Request a password reset for the given email"""
    try:
        user = User.objects.get(email=data.email)
        
        # Create reset token
        token = PasswordResetToken.objects.create(user=user)
        
        # Create reset link
        reset_link = f"https://inboxassure.online/reset-password?token={token.token}"
        
        # Send email
        email_body = f"""
        Hello {user.first_name if user.first_name else user.email.split('@')[0]},
        
        You have requested to reset your password. Please click the link below to reset your password:
        
        {reset_link}
        
        This link will expire in 1 hour.
        
        If you did not request this password reset, please ignore this email.
        
        Best regards,
        InboxAssure Team
        """
        
        send_mail(
            subject="Password Reset Request",
            message=email_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[data.email],
            fail_silently=False,
        )
        
        return {"message": "Password reset email sent"}
    except User.DoesNotExist:
        return 404, {"message": "User with this email does not exist"}

@router.post("/password-reset-verify", response={200: Dict, 404: ErrorMessage})
def verify_reset_token(request, data: PasswordResetVerifySchema):
    """Verify if a password reset token is valid"""
    try:
        reset_token = PasswordResetToken.objects.get(token=data.token)
        if reset_token.is_valid():
            return {"valid": True}
        return {"valid": False, "message": "Token has expired or has been used"}
    except PasswordResetToken.DoesNotExist:
        return 404, {"message": "Invalid token"}

@router.post("/password-reset-confirm", response={200: Dict, 400: ErrorMessage, 404: ErrorMessage})
def confirm_password_reset(request, data: PasswordResetConfirmSchema):
    """Reset the password using the token"""
    try:
        reset_token = PasswordResetToken.objects.get(token=data.token)
        
        if not reset_token.is_valid():
            return 400, {"message": "Token has expired or has been used"}
        
        # Update password
        user = reset_token.user
        user.set_password(data.new_password)
        user.save()
        
        # Mark token as used
        reset_token.used = True
        reset_token.save()
        
        return {"message": "Password has been reset successfully"}
    except PasswordResetToken.DoesNotExist:
        return 404, {"message": "Invalid token"}

@router.post("/change-password", auth=AuthBearer(), response={200: Dict, 400: ErrorMessage})
def change_password(request, data: ChangePasswordSchema):
    """Change user's password by providing old and new password"""
    try:
        user = request.auth
        # Verify old password
        if not user.check_password(data.old_password):
            return 400, {"message": "Current password is incorrect"}
        
        # Set new password
        user.set_password(data.new_password)
        user.save()
        
        return 200, {"message": "Password changed successfully"}
    except Exception as e:
        logger.error(f"Error changing password: {str(e)}")
        return 400, {"message": "Failed to change password"}

@profile_router.get("", auth=AuthBearer(), response={200: ProfileResponseSchema, 400: ErrorMessage})
def get_profile(request):
    """Get user's profile information"""
    try:
        log_to_terminal("Profile", "Get", f"User {request.auth.username} requested profile info")
        
        user = request.auth
        profile, _ = AuthProfile.objects.get_or_create(user=user)
        
        # Build profile picture URL with MEDIA_URL prefix
        profile_pic_url = None
        if profile.profile_picture:
            profile_pic_url = request.build_absolute_uri(f'/media/{profile.profile_picture.name}')
            log_to_terminal("Profile", "Get", f"Profile picture URL generated: {profile_pic_url}")
        
        response_data = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "profile_picture": profile_pic_url,
            "timezone": profile.timezone
        }
        log_to_terminal("Profile", "Get", f"Profile data retrieved successfully for user {user.username}")
        return 200, response_data
    except Exception as e:
        log_to_terminal("Profile", "Error", f"Error getting profile for user {request.auth.username}: {str(e)}")
        return 400, {"message": "Failed to get profile information"}

@profile_router.put("", auth=AuthBearer(), response={200: ProfileResponseSchema, 400: ErrorMessage})
def update_profile(request, data: UpdateProfileSchema):
    """Update user's profile information"""
    try:
        log_to_terminal("Profile", "Update", f"User {request.auth.username} requested profile update with data: {data.dict()}")
        
        user = request.auth
        profile, _ = AuthProfile.objects.get_or_create(user=user)
        
        if data.first_name is not None:
            user.first_name = data.first_name
            log_to_terminal("Profile", "Update", f"Updated first_name to: {data.first_name}")
            
        if data.last_name is not None:
            user.last_name = data.last_name
            log_to_terminal("Profile", "Update", f"Updated last_name to: {data.last_name}")
            
        if data.timezone is not None:
            try:
                import pytz
                pytz.timezone(data.timezone)  # Validate timezone
                profile.timezone = data.timezone
                profile.save()
                log_to_terminal("Profile", "Update", f"Updated timezone to: {data.timezone}")
            except pytz.exceptions.UnknownTimeZoneError:
                log_to_terminal("Profile", "Error", f"Invalid timezone provided: {data.timezone}")
                return 400, {"message": f"Invalid timezone: {data.timezone}"}
            
        user.save()
        
        response_data = {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "profile_picture": request.build_absolute_uri(profile.profile_picture.url) if profile.profile_picture else None,
            "timezone": profile.timezone
        }
        log_to_terminal("Profile", "Update", f"Profile updated successfully for user {user.username}")
        return 200, response_data
    except Exception as e:
        log_to_terminal("Profile", "Error", f"Error updating profile for user {request.auth.username}: {str(e)}")
        return 500, {"message": "Internal Server Error. Please check the server logs for details."}

def patch_put_multipart(view_func):
    def _wrapped_view(request, *args, **kwargs):
         if request.method.upper() == 'PUT':
             original_method = request.method
             # Change to POST so multipart parser works
             request.method = 'POST'
             request._load_post_and_files()
             request.method = original_method
         return view_func(request, *args, **kwargs)
    return _wrapped_view

@profile_router.put("/picture", 
    auth=AuthBearer(), 
    response={200: ProfileResponseSchema, 400: ErrorMessage, 422: dict, 500: ErrorMessage},
    openapi_extra={
        'requestBody': {
            'content': {
                'multipart/form-data': {
                    'schema': {
                        'type': 'object',
                        'properties': {
                            'file': {
                                'type': 'string',
                                'format': 'binary',
                                'description': 'Profile picture file (JPG, JPEG, PNG, or GIF, max 2.5MB)'
                            }
                        },
                        'required': ['file']
                    }
                }
            }
        }
    }
)
@patch_put_multipart
def update_profile_picture(request):
    """Upload a new profile picture"""
    try:
        log_to_terminal("Profile", "Picture", f"User {request.auth.username} started profile picture upload")
        log_to_terminal("Profile", "Picture", f"Request Method: {request.method}")
        log_to_terminal("Profile", "Picture", f"Content-Type: {request.headers.get('Content-Type', 'Not provided')}")
        
        if 'file' not in request.FILES:
            log_to_terminal("Profile", "Error", "No file provided in request")
            return 400, {"message": "No file provided"}
            
        file = request.FILES['file']
        log_to_terminal("Profile", "Picture", f"File details - Name: {file.name}, Size: {file.size}, Type: {file.content_type}")
        
        user = request.auth
        profile, created = AuthProfile.objects.get_or_create(user=user)
        log_to_terminal("Profile", "Picture", f"Profile accessed - ID: {profile.id}, Created: {created}")
        
        # Validate file extension
        allowed_extensions = ('.jpg', '.jpeg', '.png', '.gif')
        if not any(file.name.lower().endswith(ext) for ext in allowed_extensions):
            log_to_terminal("Profile", "Error", f"Invalid file extension: {file.name}")
            return 400, {"message": f"File must be one of: {', '.join(allowed_extensions)}"}
        
        # Validate file type
        if not file.content_type or not file.content_type.startswith('image/'):
            log_to_terminal("Profile", "Error", f"Invalid file type: {file.content_type}")
            return 400, {"message": f"File must be an image. Received content type: {file.content_type}"}
        
        # Validate file size (max 2.5MB)
        max_size = 2.5 * 1024 * 1024  # 2.5MB in bytes
        if file.size > max_size:
            log_to_terminal("Profile", "Error", f"File too large: {file.size} bytes")
            return 400, {"message": f"File size must be less than 2.5MB. Received: {file.size / 1024 / 1024:.2f}MB"}
        
        # Delete old profile picture if it exists
        if profile.profile_picture:
            try:
                import os
                from django.conf import settings
                
                # Get the full path of the old file
                old_file_path = os.path.join(settings.MEDIA_ROOT, str(profile.profile_picture))
                log_to_terminal("Profile", "Picture", f"Full path of old profile picture: {old_file_path}")
                
                # Check if file exists before trying to delete
                if os.path.isfile(old_file_path):
                    os.remove(old_file_path)
                    log_to_terminal("Profile", "Picture", f"Successfully deleted old profile picture: {old_file_path}")
                else:
                    log_to_terminal("Profile", "Warning", f"Old profile picture not found at: {old_file_path}")
                
                # Clear the field in the model
                profile.profile_picture = None
                profile.save()
                
            except Exception as e:
                log_to_terminal("Profile", "Warning", f"Error deleting old profile picture: {str(e)}")
        
        # Save new profile picture
        try:
            # Generate new filename
            new_path = profile_picture_path(profile, file.name)
            log_to_terminal("Profile", "Picture", f"Generated new profile picture path: {new_path}")
            
            # Save the file
            profile.profile_picture = file
            profile.save()
            
            # Verify the file was saved
            if not profile.profile_picture:
                raise Exception("Profile picture not saved properly")
                
            # Build URL with MEDIA_URL prefix
            profile_pic_url = request.build_absolute_uri(f'/media/{profile.profile_picture.name}')
            log_to_terminal("Profile", "Picture", f"New profile picture URL: {profile_pic_url}")
            
            # Double check the file exists
            import os
            from django.conf import settings
            new_file_path = os.path.join(settings.MEDIA_ROOT, str(profile.profile_picture))
            if not os.path.isfile(new_file_path):
                raise Exception(f"New profile picture file not found at: {new_file_path}")
            
            response_data = {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "profile_picture": profile_pic_url,
                "timezone": profile.timezone
            }
            log_to_terminal("Profile", "Picture", f"Profile picture updated successfully for user {user.username}")
            return 200, response_data
            
        except Exception as e:
            log_to_terminal("Profile", "Error", f"Error saving profile picture: {str(e)}")
            return 500, {"message": "Failed to save profile picture. Please try again."}
            
    except Exception as e:
        log_to_terminal("Profile", "Error", f"Unexpected error in profile picture upload: {str(e)}")
        return 500, {"message": "An unexpected error occurred. Please try again."}

@router.post("/admin/reset-password", auth=AuthBearer(), response={200: Dict, 400: ErrorMessage, 403: ErrorMessage})
def admin_reset_password(request, data: AdminPasswordResetSchema):
    """Admin endpoint to reset a user's password without requiring the old password"""
    try:
        # Check if the requesting user is an admin
        if not request.auth.is_staff and not request.auth.is_superuser:
            log_to_terminal("Admin", "PasswordReset", f"Unauthorized attempt by {request.auth.username}")
            return 403, {"message": "You do not have permission to perform this action"}
        
        # Get the target user
        try:
            target_user = User.objects.get(id=data.user_id)
        except User.DoesNotExist:
            log_to_terminal("Admin", "PasswordReset", f"User with ID {data.user_id} not found")
            return 400, {"message": f"User with ID {data.user_id} not found"}
        
        # Set new password
        target_user.set_password(data.new_password)
        target_user.save()
        
        log_to_terminal("Admin", "PasswordReset", f"Admin {request.auth.username} reset password for user {target_user.username}")
        return 200, {"message": f"Password for user {target_user.username} has been reset successfully"}
    except Exception as e:
        logger.error(f"Error in admin password reset: {str(e)}")
        return 400, {"message": "Failed to reset password"}

@router.get("/admin/users", auth=AuthBearer(), response={200: List[UserListItemSchema], 403: ErrorMessage})
def admin_list_users(request):
    """Admin endpoint to list all users in the system"""
    try:
        # Check if the requesting user is an admin
        if not request.auth.is_staff and not request.auth.is_superuser:
            log_to_terminal("Admin", "ListUsers", f"Unauthorized attempt by {request.auth.username}")
            return 403, {"message": "You do not have permission to perform this action"}
        
        # Get all users
        users = User.objects.all().order_by('-date_joined')
        
        # Format the response
        user_list = []
        for user in users:
            user_list.append({
                "id": user.id,
                "username": user.username,
                "email": user.email,
                "first_name": user.first_name,
                "last_name": user.last_name,
                "is_staff": user.is_staff,
                "is_superuser": user.is_superuser,
                "date_joined": user.date_joined,
                "last_login": user.last_login
            })
        
        log_to_terminal("Admin", "ListUsers", f"Admin {request.auth.username} listed all users")
        return 200, user_list
    except Exception as e:
        logger.error(f"Error in admin list users: {str(e)}")
        return 403, {"message": "Failed to list users"}

@router.post("/admin/toggle-staff", auth=AuthBearer(), response={200: Dict, 400: ErrorMessage, 403: ErrorMessage})
def admin_toggle_staff(request, data: AdminToggleSchema):
    """Admin endpoint to toggle staff status for a user"""
    try:
        # Check if the requesting user is a superuser
        if not request.auth.is_superuser:
            log_to_terminal("Admin", "ToggleStaff", f"Unauthorized attempt by {request.auth.username}")
            return 403, {"message": "Only superusers can change staff status"}
        
        # Get the target user
        try:
            target_user = User.objects.get(id=data.user_id)
        except User.DoesNotExist:
            log_to_terminal("Admin", "ToggleStaff", f"User with ID {data.user_id} not found")
            return 400, {"message": f"User with ID {data.user_id} not found"}
        
        # Don't allow demoting yourself
        if request.auth.id == target_user.id and not data.is_staff:
            return 400, {"message": "You cannot remove your own admin status"}
        
        # Update staff status
        target_user.is_staff = data.is_staff
        target_user.save()
        
        action = "promoted to admin" if data.is_staff else "demoted from admin"
        log_to_terminal("Admin", "ToggleStaff", f"Admin {request.auth.username} {action} user {target_user.username}")
        return 200, {"message": f"User {target_user.username} {action} successfully"}
    except Exception as e:
        logger.error(f"Error in toggle staff status: {str(e)}")
        return 400, {"message": "Failed to update staff status"} 