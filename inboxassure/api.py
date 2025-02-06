from ninja import NinjaAPI
from authentication.api import router as auth_router, profile_router
from settings.api import router as settings_router
from spamcheck.api import router as spamcheck_router
from ninja.security import HttpBearer
from typing import Any, Optional
import jwt
from django.conf import settings
from django.contrib.auth import get_user_model

User = get_user_model()

class CustomAuthBearer(HttpBearer):
    def authenticate(self, request, token: str) -> Optional[Any]:
        # Skip authentication for OPTIONS requests
        if request.method == 'OPTIONS':
            return None
            
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            return user
        except:
            return None

api = NinjaAPI(
    urls_namespace="auth",
    csrf=False,
    auth=CustomAuthBearer(),
    docs_url=None
)

# Mount routers without trailing slashes
api.add_router("auth", auth_router)
api.add_router("profile", profile_router)
api.add_router("settings", settings_router)
api.add_router("spamcheck", spamcheck_router)

# Add OPTIONS handler for preflight requests
@api.api_operation(["OPTIONS"], "/{path:path}", auth=None)
def options_handler(request, path: str) -> Any:
    response = api.create_response(request, content={})
    return response 