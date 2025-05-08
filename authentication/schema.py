from ninja import Schema
from typing import Optional
from ninja.files import UploadedFile
from datetime import datetime

class TokenSchema(Schema):
    token: str
    message: str

class ErrorMessage(Schema):
    message: str

class LoginSchema(Schema):
    username: str
    password: str

class ChangePasswordSchema(Schema):
    old_password: str
    new_password: str

class UpdateProfileSchema(Schema):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    timezone: Optional[str] = None

class ProfileResponseSchema(Schema):
    first_name: str
    last_name: str
    email: str
    profile_picture: Optional[str] = None
    timezone: Optional[str] = None

class PasswordResetRequestSchema(Schema):
    email: str

class PasswordResetVerifySchema(Schema):
    token: str

class PasswordResetConfirmSchema(Schema):
    token: str
    new_password: str

class AdminPasswordResetSchema(Schema):
    user_id: int
    new_password: str

class UserListItemSchema(Schema):
    id: int
    username: str
    email: str
    first_name: str = None
    last_name: str = None
    is_staff: bool
    is_superuser: bool
    date_joined: datetime
    last_login: Optional[datetime] = None

class AdminToggleSchema(Schema):
    user_id: int
    is_staff: bool 