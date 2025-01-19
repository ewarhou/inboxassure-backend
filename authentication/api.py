from django.contrib.auth import get_user_model, authenticate
from django.contrib.auth.hashers import make_password
from ninja import Router, Schema
from ninja.security import HttpBearer
import jwt
from datetime import datetime, timedelta
from django.conf import settings
from django.db import connections
from ninja.responses import Response

User = get_user_model()
router = Router()

class SignUpSchema(Schema):
    username: str
    email: str
    password: str
    first_name: str = None
    last_name: str = None

class LoginSchema(Schema):
    username: str
    password: str

class TokenSchema(Schema):
    access_token: str
    token_type: str

class ErrorSchema(Schema):
    detail: str

def verify_client_email(email):
    with connections['default'].cursor() as cursor:
        cursor.execute(
            "SELECT COUNT(*) FROM inboxassure_clients WHERE client_email = %s",
            [email]
        )
        count = cursor.fetchone()[0]
        return count > 0

@router.post("/register", response={200: TokenSchema, 400: ErrorSchema})
def register(request, data: SignUpSchema):
    # First verify if the email exists in inboxassure_clients
    if not verify_client_email(data.email):
        return 400, {"detail": "Email not found in our client database. Please contact support."}
    
    if User.objects.filter(username=data.username).exists():
        return 400, {"detail": "Username already registered"}
    
    if User.objects.filter(email=data.email).exists():
        return 400, {"detail": "Email already registered"}
    
    user = User.objects.create(
        username=data.username,
        email=data.email,
        password=make_password(data.password),
        first_name=data.first_name or "",
        last_name=data.last_name or ""
    )
    
    token = generate_token(user)
    return 200, {"access_token": token, "token_type": "bearer"}

@router.post("/login", response={200: TokenSchema, 401: ErrorSchema})
def login(request, data: LoginSchema):
    user = authenticate(username=data.username, password=data.password)
    if not user:
        return 401, {"detail": "Invalid credentials"}
    
    token = generate_token(user)
    return 200, {"access_token": token, "token_type": "bearer"}

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