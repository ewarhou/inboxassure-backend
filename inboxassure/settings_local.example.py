# This is an example of a local settings file.
# Copy this file to settings_local.py (in the same directory)
# and customize it for your local development environment.
# settings_local.py is ignored by Git, so your local specific settings
# and secrets won't be committed to the repository.

# --- Database Configuration (Example for local development) ---
# Uncomment and modify the following lines if you want to use a different
# database configuration for your local setup. Otherwise, the default
# from the main settings.py will be used.

# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.mysql',
#         'NAME': 'local_inboxassure_db_name',  # Your local database name
#         'USER': 'local_db_user',              # Your local database user
#         'PASSWORD': 'local_db_password',      # Your local database password
#         'HOST': 'localhost',                  # Or '127.0.0.1' or your local DB host
#         'PORT': '3306',                       # Your local database port
#     }
# }

# --- DEBUG Configuration ---
# You might want to ensure DEBUG is True locally, even if the main settings.py defaults to False for production.
# DEBUG = True

# --- Other local overrides ---
# Example:
# OPENROUTER_API_KEY = "your_local_openrouter_api_key_if_different"
# BISON_API_BASE_URL = "http://localhost:8001" # If running a local Bison instance 