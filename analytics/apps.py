from django.apps import AppConfig

class AnalyticsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'analytics'
    
    def ready(self):
        """
        Import signals when the app is ready.
        This ensures that the signal handlers are registered.
        """
        import analytics.signals 