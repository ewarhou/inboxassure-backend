import requests
import random
import logging
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from .models import BisonWebhookData, BisonBounces, BisonWebhook
from settings.models import UserBison # Import UserBison

logger = logging.getLogger(__name__)
User = get_user_model()

# Updated bounce bucket names
BOUNCE_BUCKETS = [
    'invalid_address',
    'reputation_block',
    'auth_failure',
    'policy_reject',
    'temp_deferral',
    'infra_other'
]

# Modified to accept base_url and api_token
def get_bison_bounce_reply(base_url, api_token, campaign_id, scheduled_email_id):
    """Fetches bounce reply text from the Bison API using provided credentials."""
    if not base_url or not api_token:
        logger.error("Bison base_url or api_token not provided.")
        return None

    api_url = f"{base_url.rstrip('/')}/api/replies"
    headers = {
        'Authorization': f'Bearer {api_token}', # Use passed token
        'Accept': 'application/json'
    }
    params = {
        'folder': 'bounced',
        'campaign_id': campaign_id
    }

    try:
        response = requests.get(api_url, headers=headers, params=params, timeout=10)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        replies = response.json().get('data', [])

        for reply in replies:
            if reply.get('scheduled_email_id') == scheduled_email_id:
                return reply.get('text_body') # Return the text body of the matched reply

        logger.warning(f"No matching bounce reply found for campaign {campaign_id}, scheduled_email {scheduled_email_id} in {base_url}")
        return None

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Bison API ({api_url}): {e}")
        return None
    except Exception as e:
        logger.error(f"Error processing Bison API response from {api_url}: {e}")
        return None

@receiver(post_save, sender=BisonWebhookData)
def process_bounce_webhook(sender, instance: BisonWebhookData, created, **kwargs):
    """Signal handler to create a BisonBounces record when a bounce webhook is received."""
    if not created:
        return # Only process newly created webhook data entries

    try:
        payload = instance.payload
        event_type = payload.get('event', {}).get('type')

        if event_type == 'EMAIL_BOUNCED':
            logger.info(f"Processing EMAIL_BOUNCED event for webhook data ID: {instance.id}")
            data = payload.get('data', {})
            webhook_instance = instance.webhook
            user_instance = webhook_instance.user

            # Extract workspace_id (to be saved directly) and workspace_name (for lookup)
            event_data = payload.get('event', {})
            workspace_id_from_payload = event_data.get('workspace_id') # The ID from Bison to store
            workspace_name = event_data.get('workspace_name') # The name to use for lookup

            if not workspace_name:
                logger.error(f"Missing workspace_name in payload for webhook data ID: {instance.id}. Cannot fetch API key.")
                return
            if workspace_id_from_payload is None: # Check if it exists, even if null
                 logger.warning(f"Missing workspace_id in payload for webhook data ID: {instance.id}. Will store null if bounce record is created.")

            # Get the specific UserBison instance using the workspace_name
            try:
                user_bison_org = UserBison.objects.get(
                    user=user_instance, 
                    bison_organization_name=workspace_name, 
                    bison_organization_status=True
                )
                bison_api_token = user_bison_org.bison_organization_api_key
                bison_base_url = user_bison_org.base_url
            except UserBison.DoesNotExist:
                logger.error(f"Active UserBison organization with name '{workspace_name}' not found for user {user_instance.username}. Cannot process bounce.")
                return
            except UserBison.MultipleObjectsReturned:
                 logger.error(f"Multiple active UserBison organizations found with name '{workspace_name}' for user {user_instance.username}. Ambiguous lookup.")
                 return # Or handle ambiguity differently
            except Exception as e:
                 logger.error(f"Error fetching UserBison organization '{workspace_name}' for user {user_instance.username}: {e}")
                 return

            # Extract other data safely
            scheduled_email = data.get('scheduled_email', {})
            lead = data.get('lead', {})
            campaign = data.get('campaign', {})
            sender_email_info = data.get('sender_email', {})

            scheduled_email_id = scheduled_email.get('id')
            campaign_id = campaign.get('id')

            # Fetch bounce reply from Bison API using the user's token and base URL
            bounce_reply_text = None
            if campaign_id and scheduled_email_id:
                bounce_reply_text = get_bison_bounce_reply(bison_base_url, bison_api_token, campaign_id, scheduled_email_id)
            else:
                logger.warning(f"Missing campaign_id or scheduled_email_id in payload for webhook data ID: {instance.id}")

            # Create the BisonBounces record
            BisonBounces.objects.create(
                user=user_instance,
                webhook=webhook_instance,
                workspace_bison_id=workspace_id_from_payload, # Store the ID from payload directly
                workspace_name=workspace_name,
                email_subject=scheduled_email.get('email_subject'),
                email_body=scheduled_email.get('email_body'),
                lead_email=lead.get('email'),
                campaign_bison_id=campaign_id,
                campaign_name=campaign.get('name'),
                sender_bison_id=sender_email_info.get('id'),
                sender_email=sender_email_info.get('email'),
                bounce_reply=bounce_reply_text,
                bounce_bucket=random.choice(BOUNCE_BUCKETS)
            )
            logger.info(f"Successfully created BisonBounces record for webhook data ID: {instance.id} using org name '{workspace_name}'")

    except KeyError as e:
        logger.error(f"Missing key in webhook payload for BisonWebhookData ID {instance.id}: {e}")
    except Exception as e:
        logger.error(f"Error processing bounce webhook signal for BisonWebhookData ID {instance.id}: {e}") 