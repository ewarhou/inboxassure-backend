from ninja import Schema
from typing import Optional
from ninja.files import UploadedFile

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