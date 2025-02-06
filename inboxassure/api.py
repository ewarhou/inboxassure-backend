from ninja import NinjaAPI
from authentication.api import router as auth_router, profile_router
from settings.api import router as settings_router
from spamcheck.api import router as spamcheck_router
from ninja.security import HttpBearer
from typing import Any

class CORSMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response["Access-Control-Allow-Origin"] = "*"
        response["Access-Control-Allow-Methods"] = "*"
        response["Access-Control-Allow-Headers"] = "*"
        return response

api = NinjaAPI(
    urls_namespace="auth",
    csrf=False,
    auth=HttpBearer(),
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