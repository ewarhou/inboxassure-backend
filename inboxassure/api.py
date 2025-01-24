from ninja import NinjaAPI
from authentication.api import router as auth_router
from analytics.api import router as analytics_router
from settings.api import router as settings_router
from spamcheck.api import router as spamcheck_router

api = NinjaAPI()
api.add_router("/auth/", auth_router)
api.add_router("/analytics/", analytics_router)
api.add_router("/settings/", settings_router)
api.add_router("/spamcheck/", spamcheck_router) 