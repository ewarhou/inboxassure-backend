# Local settings for inboxassure project.
# This file is NOT committed to version control.

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': 'inboxassure',  # Development database name
        'USER': 'amine',
        'PASSWORD': 'Warhou19981@',
        'HOST': '64.227.20.217',
        'PORT': '3306',
    }
}

# For local development, DEBUG is usually True
DEBUG = True

# --- Other local overrides ---
# Example:
# OPENROUTER_API_KEY = "your_local_openrouter_api_key_if_different"
# BISON_API_BASE_URL = "http://localhost:8001" # If running a local Bison instance
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend' # For testing emails 