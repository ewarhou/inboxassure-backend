from ninja import NinjaAPI
from authentication.api import router as auth_router, profile_router
from settings.api import router as settings_router
from spamcheck.api import router as spamcheck_router

api = NinjaAPI(
    title="InboxAssure API",
    description="API documentation for InboxAssure backend services",
    version="1.0.0",
    urls_namespace="auth",
    csrf=False,
    auth=None,
    docs_url="/api/docs",
)

api.add_router("/auth/", auth_router)
api.add_router("/profile/", profile_router)
api.add_router("/settings/", settings_router)
api.add_router("/spamcheck/", spamcheck_router) 