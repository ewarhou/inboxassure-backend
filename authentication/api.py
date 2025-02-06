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
from typing import Dict, Optional
from .models import PasswordResetToken, AuthProfile
from .schema import (
    TokenSchema, ErrorMessage, PasswordResetRequestSchema, 
    PasswordResetVerifySchema, PasswordResetConfirmSchema, 
    ChangePasswordSchema, UpdateProfileSchema, ProfileResponseSchema
)

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
        # Get user by email
        user = User.objects.get(email=data.email)
        # Authenticate using username and password
        authenticated_user = authenticate(username=user.username, password=data.password)
        
        if not authenticated_user:
            logger.warning(f"Failed login attempt for email: {data.email}")
            return 401, {"detail": "Invalid credentials"}
        
        logger.info(f"Successful login for email: {data.email}")
        token = generate_token(authenticated_user)
        return 200, {"access_token": token, "token_type": "bearer"}
    except User.DoesNotExist:
        logger.warning(f"Login attempt with non-existent email: {data.email}")
        return 401, {"detail": "Invalid credentials"}
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

@profile_router.get("/", auth=AuthBearer(), response={200: ProfileResponseSchema, 400: ErrorMessage})
def get_profile(request):
    """Get user's profile information"""
    try:
        user = request.auth
        profile, _ = AuthProfile.objects.get_or_create(user=user)
        
        # Build profile picture URL with MEDIA_URL prefix
        profile_pic_url = None
        if profile.profile_picture:
            profile_pic_url = request.build_absolute_uri(f'/media/{profile.profile_picture.name}')
            logger.info(f"Generated profile picture URL: {profile_pic_url}")
        
        return 200, {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "profile_picture": profile_pic_url,
            "timezone": profile.timezone
        }
    except Exception as e:
        logger.error(f"Error getting profile: {str(e)}")
        return 400, {"message": "Failed to get profile information"}

@profile_router.put("/", auth=AuthBearer(), response={200: ProfileResponseSchema, 400: ErrorMessage})
def update_profile(request, data: UpdateProfileSchema):
    """Update user's profile information"""
    try:
        user = request.auth
        profile, _ = AuthProfile.objects.get_or_create(user=user)
        
        if data.first_name is not None:
            user.first_name = data.first_name
        if data.last_name is not None:
            user.last_name = data.last_name
        if data.timezone is not None:
            try:
                import pytz
                pytz.timezone(data.timezone)  # Validate timezone
                profile.timezone = data.timezone
                profile.save()
            except pytz.exceptions.UnknownTimeZoneError:
                return 400, {"message": f"Invalid timezone: {data.timezone}"}
            
        user.save()
        
        return 200, {
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "profile_picture": request.build_absolute_uri(profile.profile_picture.url) if profile.profile_picture else None,
            "timezone": profile.timezone
        }
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        user_id = user.id if user and hasattr(user, 'id') else 'unknown'
        logger.error(f"Error updating profile for user {user_id}: {str(e)}\nTraceback: {tb}")
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
def update_profile_picture(
    request,
    file: UploadedFile = File(..., description="Profile picture file (JPG, JPEG, PNG, or GIF, max 2.5MB)")
):
    """
    Upload a new profile picture
    
    Parameters:
        - file: Image file (JPG, JPEG, PNG, or GIF)
          - Maximum size: 2.5MB
          - Must be sent as form-data with field name 'file'
    
    Returns:
        - 200: Profile updated successfully
        - 400: Invalid file type or size
        - 422: Missing or invalid file
        - 500: Server error
    """
    try:
        logger.info("=== Profile Picture Upload Debug ===")
        logger.info(f"Request Method: {request.method}")
        logger.info(f"Content-Type: {request.headers.get('Content-Type', 'Not provided')}")
        
        if not file:
            logger.error("No file provided in request")
            return 400, {"message": "No file provided"}
            
        logger.info(f"File details:")
        logger.info(f"- Name: {file.name}")
        logger.info(f"- Size: {file.size}")
        logger.info(f"- Content Type: {file.content_type}")
        
        user = request.auth
        profile, _ = AuthProfile.objects.get_or_create(user=user)
        
        # Validate file extension
        allowed_extensions = ('.jpg', '.jpeg', '.png', '.gif')
        if not any(file.name.lower().endswith(ext) for ext in allowed_extensions):
            logger.error(f"Invalid file extension: {file.name}")
            return 400, {"message": f"File must be one of: {', '.join(allowed_extensions)}"}
        
        # Validate file type
        if not file.content_type or not file.content_type.startswith('image/'):
            logger.error(f"Invalid file type: {file.content_type}")
            return 400, {"message": f"File must be an image. Received content type: {file.content_type}"}
        
        # Validate file size (max 2.5MB)
        max_size = 2.5 * 1024 * 1024  # 2.5MB in bytes
        if file.size > max_size:
            logger.error(f"File too large: {file.size} bytes")
            return 400, {"message": f"File size must be less than 2.5MB. Received: {file.size / 1024 / 1024:.2f}MB"}
        
        # Delete old profile picture if it exists
        if profile.profile_picture:
            try:
                old_path = profile.profile_picture.path
                profile.profile_picture.delete(save=False)
                logger.info(f"Deleted old profile picture: {old_path}")
            except Exception as e:
                logger.warning(f"Error deleting old profile picture: {str(e)}")
        
        # Save new profile picture
        try:
            profile.profile_picture = file
            profile.save()
            logger.info("Successfully saved new profile picture")
            
            # Build URL with MEDIA_URL prefix
            profile_pic_url = request.build_absolute_uri(f'/media/{profile.profile_picture.name}')
            logger.info(f"Generated profile picture URL: {profile_pic_url}")
            
            return 200, {
                "first_name": user.first_name,
                "last_name": user.last_name,
                "email": user.email,
                "profile_picture": profile_pic_url,
                "timezone": profile.timezone
            }
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            user_id = user.id if user and hasattr(user, 'id') else 'unknown'
            logger.error(f"Error saving profile picture for user {user_id}: {str(e)}\nTraceback: {tb}")
            return 500, {"message": "Failed to save profile picture. Please try again."}
            
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logger.error(f"Unexpected error in profile picture upload: {str(e)}\nTraceback: {tb}")
        return 500, {"message": "An unexpected error occurred. Please try again."} 