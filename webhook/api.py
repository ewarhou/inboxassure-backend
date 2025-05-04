from ninja import Router, Schema
from ninja.security import HttpBearer
from django.shortcuts import get_object_or_404
from django.http import HttpRequest, HttpResponse, JsonResponse
# from django.contrib.auth.models import User # No longer needed directly here
from django.views.decorators.csrf import csrf_exempt
import json

from .models import BisonWebhook, BisonWebhookData
from .schemas import WebhookUrlSchema
from inboxassure.settings import ALLOWED_HOSTS # To construct the full URL
from authentication.authorization import AuthBearer # Import the correct AuthBearer

# Remove the placeholder GlobalAuth class
# class GlobalAuth(HttpBearer):
#     ...

router = Router(tags=['Webhook'])

# Use https and determine the domain dynamically or use a specific one
# Let's assume the first ALLOWED_HOST is the correct one for production/dev
# Adjust if necessary, e.g., read from an environment variable
BASE_URL = f"https://{ALLOWED_HOSTS[0]}"

@router.post("/generate", response=WebhookUrlSchema, auth=AuthBearer()) # Use AuthBearer
def generate_webhook(request: HttpRequest):
    """Generates a new unique webhook URL for the authenticated user, replacing any existing one."""
    user = request.auth # Now request.auth should be the User object
    # Delete existing webhooks for the user to ensure only one active URL
    BisonWebhook.objects.filter(user=user).delete()
    # Create a new webhook
    new_webhook = BisonWebhook.objects.create(user=user)
    webhook_url = f"{BASE_URL}/api/webhook/receive/{new_webhook.webhook_id}/"
    return {"webhook_url": webhook_url}

@router.get("/", response=WebhookUrlSchema, auth=AuthBearer()) # Use AuthBearer
def get_webhook(request: HttpRequest):
    """Retrieves the current webhook URL for the authenticated user."""
    user = request.auth
    webhook = get_object_or_404(BisonWebhook, user=user)
    webhook_url = f"{BASE_URL}/api/webhook/receive/{webhook.webhook_id}/"
    return {"webhook_url": webhook_url}

# This is the actual endpoint that listens for incoming webhook data
@csrf_exempt # Important for webhooks which won't have CSRF tokens
@router.post("/receive/{webhook_id}/")
def receive_webhook(request: HttpRequest, webhook_id: str):
    """Receives incoming data from a third-party service via POST request."""
    webhook = get_object_or_404(BisonWebhook, webhook_id=webhook_id)

    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse("Invalid JSON payload", status=400)

    # Store the received data
    BisonWebhookData.objects.create(
        webhook=webhook,
        payload=payload
    )

    # log_to_terminal("Webhook", "Received", f"Data received for webhook {webhook_id}")
    return HttpResponse("Webhook data received successfully.", status=200) 