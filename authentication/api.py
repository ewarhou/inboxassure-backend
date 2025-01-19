from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.hashers import make_password
from ninja import Router, Schema
from ninja.security import HttpBearer
import jwt
from datetime import datetime, timedelta
from django.conf import settings
from django.db import connections
from ninja.responses import Response
import logging

logger = logging.getLogger(__name__)

User = get_user_model()
router = Router()

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