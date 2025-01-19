from ninja import Schema
from typing import Optional

class TokenSchema(Schema):
    token: str
    message: str

class ErrorMessage(Schema):
    message: str

class LoginSchema(Schema):
    username: str
    password: str

class PasswordResetRequestSchema(Schema):
    email: str

class PasswordResetVerifySchema(Schema):
    token: str

class PasswordResetConfirmSchema(Schema):
    token: str
    new_password: str 