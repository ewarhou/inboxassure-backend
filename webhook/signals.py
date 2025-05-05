import requests
import random
import logging
import json
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from django.contrib.auth import get_user_model
from django.shortcuts import get_object_or_404

from .models import BisonWebhookData, BisonBounces, BisonWebhook
from settings.models import UserBison # Import UserBison
from call_openrouter import call_openrouter # Import the AI function

logger = logging.getLogger(__name__)
User = get_user_model()

# Updated bounce bucket names
BOUNCE_BUCKETS = [
    'invalid_address',
    'reputation_block',
    'auth_failure',
    'policy_reject',
    'temp_deferral',
    'infra_other',
    'unknown'
]

# Modified to accept base_url and api_token and handle pagination
def get_bison_bounce_reply(base_url, api_token, campaign_id, scheduled_email_id):
    """Fetches bounce reply text from the Bison API using provided credentials, handling pagination."""
    if not base_url or not api_token:
        logger.error("Bison base_url or api_token not provided.")
        return None

    api_url = f"{base_url.rstrip('/')}/api/replies"
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Accept': 'application/json'
    }
    params = {
        'folder': 'bounced',
        'campaign_id': campaign_id,
        'page': 1
    }

    max_pages_to_check = 10 # Safety limit to prevent potential infinite loops

    while params['page'] <= max_pages_to_check:
        logger.debug(f"Fetching bounce replies page {params['page']} for campaign {campaign_id}")
        try:
            response = requests.get(api_url, headers=headers, params=params, timeout=15) # Increased timeout slightly
            response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            
            try:
                response_data = response.json()
            except json.JSONDecodeError:
                 logger.error(f"Failed to decode JSON response from {api_url} page {params['page']}. Body: {response.text}")
                 return None # Cannot proceed without valid JSON

            replies = response_data.get('data', [])
            meta = response_data.get('meta', {})
            current_page = meta.get('current_page', params['page'])
            last_page = meta.get('last_page', current_page) # Assume current is last if not provided

            if not replies and params['page'] == 1:
                logger.warning(f"No bounce replies found on first page for campaign {campaign_id} in {base_url}")
                return None # No bounces found at all

            for reply in replies:
                if reply.get('scheduled_email_id') == scheduled_email_id:
                    logger.info(f"Found matching bounce reply on page {current_page} for scheduled_email {scheduled_email_id}")
                    return reply.get('text_body') # Return the text body of the matched reply

            # If not found on this page, check if we should fetch the next page
            if current_page >= last_page:
                logger.warning(f"Checked all pages ({last_page}) - No matching bounce reply found for campaign {campaign_id}, scheduled_email {scheduled_email_id} in {base_url}")
                return None # Reached the last page

            # Prepare for the next page
            params['page'] += 1

        except requests.exceptions.Timeout:
            logger.error(f"Timeout calling Bison API ({api_url}, page {params['page']})")
            return None # Stop processing on timeout
        except requests.exceptions.RequestException as e:
            logger.error(f"Error calling Bison API ({api_url}, page {params['page']}): {e}")
            return None # Stop processing on request error
        except Exception as e:
            logger.error(f"Error processing Bison API response from {api_url}, page {params['page']}: {e}")
            return None # Stop processing on unexpected error
            
    logger.error(f"Reached max page limit ({max_pages_to_check}) while searching for bounce reply. Aborting search.")
    return None # Reached safety limit

# --- New function to get sender tags ---
def get_bison_sender_tags(base_url, api_token, sender_email):
    """Fetches sender tags from the Bison API."""
    if not base_url or not api_token or not sender_email:
        logger.error("Bison base_url, api_token, or sender_email not provided for tag fetch.")
        return []

    # URL encode the email address in case it contains special characters
    encoded_email = requests.utils.quote(sender_email)
    api_url = f"{base_url.rstrip('/')}/api/sender-emails/{encoded_email}"
    headers = {
        'Authorization': f'Bearer {api_token}',
        'Accept': 'application/json'
    }

    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        if response.status_code == 404:
            logger.warning(f"Sender email {sender_email} not found in Bison API ({api_url}). Cannot fetch tags.")
            return []
        response.raise_for_status() # Raise HTTPError for other bad responses

        try:
            response_data = response.json()
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON response when fetching tags from {api_url}. Body: {response.text}")
            return []
        
        tags_data = response_data.get('data', {}).get('tags', [])
        tag_names = [tag.get('name') for tag in tags_data if tag.get('name')] 
        logger.debug(f"Fetched tags for {sender_email}: {tag_names}")
        return tag_names

    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling Bison API for tags ({api_url}): {e}")
        return []
    except Exception as e:
        logger.error(f"Error processing Bison API tag response from {api_url}: {e}")
        return []

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
            sender_email = sender_email_info.get('email') # Get sender email

            # --- Extract domain from sender_email ---
            domain = None
            if sender_email and '@' in sender_email:
                try:
                    domain = sender_email.split('@')[1]
                except IndexError:
                    logger.warning(f"Could not extract domain from sender_email: {sender_email}")
            
            # --- Fetch sender tags from Bison API ---
            sender_tags = []
            if sender_email:
                sender_tags = get_bison_sender_tags(bison_base_url, bison_api_token, sender_email)
            else:
                 logger.warning(f"Missing sender_email in payload for webhook data ID: {instance.id}. Cannot fetch tags.")

            # Fetch bounce reply from Bison API using the user's token and base URL
            bounce_reply_text = None
            if campaign_id and scheduled_email_id:
                bounce_reply_text = get_bison_bounce_reply(bison_base_url, bison_api_token, campaign_id, scheduled_email_id)
            else:
                logger.warning(f"Missing campaign_id or scheduled_email_id in payload for webhook data ID: {instance.id}")

            # --- Classify Bounce Bucket using OpenRouter ---            
            classified_bucket = None
            if bounce_reply_text:
                logger.info(f"Attempting bounce classification for webhook data ID: {instance.id}")
                classification_prompt = f"""
                    You are an email-deliverability classifier.

                    Return **exactly one** of the following bucket names
                    (in lower-case snake_case, no extra characters and no extra explanations or additional text):

                    • invalid_address      – recipient doesn't exist / user unknown  
                    • reputation_block     – sender blocked for spam / bad reputation  
                    • auth_failure         – SPF, DKIM or DMARC failed  
                    • policy_reject        – content or attachment policy violation  
                    • temp_deferral        – temporary delay, rate-limit, mailbox full  
                    • infra_other          – DNS, TLS or generic infrastructure error  

                    Here is the bounce message : 
                    {bounce_reply_text}
                """
                
                # Choose model - using the free tier as per call_openrouter.py default
                # Or specify another like "openai/gpt-4o"
                model_to_use = "meta-llama/llama-4-scout:free" 
                
                ai_response = call_openrouter(classification_prompt, model=model_to_use)
                
                if ai_response:
                    cleaned_response = ai_response.strip().lower()
                    # Check against all buckets EXCEPT unknown
                    valid_buckets_for_ai = [b for b in BOUNCE_BUCKETS if b != 'unknown'] 
                    if cleaned_response in valid_buckets_for_ai:
                        classified_bucket = cleaned_response
                        logger.info(f"Successfully classified bounce bucket as: {classified_bucket} for webhook data ID: {instance.id}")
                    else:
                        logger.warning(f"AI response '{cleaned_response}' is not a valid bucket name. Falling back to 'unknown' for webhook data ID: {instance.id}")
                else:
                    logger.warning(f"Failed to get classification from AI. Falling back to 'unknown' for webhook data ID: {instance.id}")
            else:
                logger.warning(f"No bounce reply text found. Cannot classify bucket. Falling back to 'unknown' for webhook data ID: {instance.id}")

            # Determine final bucket (use classified if available, else 'unknown')
            bounce_bucket_to_save = classified_bucket if classified_bucket else 'unknown'
            if not classified_bucket:
                 logger.info(f"Assigning fallback bounce bucket: {bounce_bucket_to_save} for webhook data ID: {instance.id}")

            # Create the BisonBounces record
            BisonBounces.objects.create(
                user=user_instance,
                webhook=webhook_instance,
                workspace_bison_id=workspace_id_from_payload,
                workspace_name=workspace_name,
                email_subject=scheduled_email.get('email_subject'),
                email_body=scheduled_email.get('email_body'),
                lead_email=lead.get('email'),
                campaign_bison_id=campaign_id,
                campaign_name=campaign.get('name'),
                sender_bison_id=sender_email_info.get('id'),
                sender_email=sender_email,
                domain=domain,
                tags=sender_tags,
                bounce_reply=bounce_reply_text,
                bounce_bucket=bounce_bucket_to_save # Use determined bucket ('unknown' if failed)
            )
            logger.info(f"Successfully created BisonBounces record for webhook data ID: {instance.id} using org name '{workspace_name}'")

    except KeyError as e:
        logger.error(f"Missing key in webhook payload for BisonWebhookData ID {instance.id}: {e}")
    except Exception as e:
        logger.error(f"Error processing bounce webhook signal for BisonWebhookData ID {instance.id}: {e}") 