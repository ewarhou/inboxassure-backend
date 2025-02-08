from ninja import NinjaAPI
from authentication.api import router as auth_router, profile_router
from settings.api import router as settings_router
from spamcheck.api import router as spamcheck_router
from django.http import HttpRequest

api = NinjaAPI(urls_namespace="auth")

@api.get("/test")
def test_endpoint(request: HttpRequest):
    """Test endpoint to verify deployment"""
    return {
        "status": "success",
        "message": "API is working correctly!",
        "environment": "production",
        "server_time": "UTC",
        "endpoints_available": [
            "/api/test",
            "/api/auth/*",
            "/api/profile/*",
            "/api/settings/*",
            "/api/spamcheck/*"
        ]
    }

api.add_router("/auth/", auth_router)
api.add_router("/profile/", profile_router)
api.add_router("/settings/", settings_router)
api.add_router("/spamcheck/", spamcheck_router) 