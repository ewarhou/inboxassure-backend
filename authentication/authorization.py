from typing import Optional, Any
from ninja.security import HttpBearer
from django.contrib.auth import get_user_model
import jwt
from django.conf import settings

User = get_user_model()

class AuthBearer(HttpBearer):
    def authenticate(self, request, token: str) -> Optional[Any]:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            return user
        except (jwt.PyJWTError, User.DoesNotExist):
            return None 