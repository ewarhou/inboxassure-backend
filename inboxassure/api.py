from ninja import NinjaAPI
from authentication.api import router as auth_router
from settings.api import router as settings_router, profile_router
from spamcheck.api import router as spamcheck_router

api = NinjaAPI()
api.add_router("/auth/", auth_router)
api.add_router("/settings/", settings_router)
api.add_router("/profile/", profile_router)
api.add_router("/spamcheck/", spamcheck_router) 