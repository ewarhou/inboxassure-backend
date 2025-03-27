import aiohttp
import asyncio
import json
from django.core.serializers.json import DjangoJSONEncoder
from settings.api import log_to_terminal
from django.db.models import Avg, Count, Q
from django.utils import timezone
import requests
from datetime import datetime
from uuid import UUID
from decimal import Decimal

async def send_webhook(user_settings, spamcheck, reports_data):
    """
    Send webhook notification for completed spamcheck in a non-blocking way (fire and forget)
    
    Args:
        user_settings: UserSettings instance
        spamcheck: UserSpamcheckBison instance
        reports_data: List of UserSpamcheckBisonReport data
    """
    if not user_settings or not user_settings.webhook_url:
        print(f"⚠️ No webhook URL configured for user {getattr(user_settings, 'user', None)}")
        log_to_terminal("Webhook", "Warning", f"No webhook URL configured for user {getattr(user_settings, 'user', None)}")
        return
    
    print(f"📤 Initiating webhook send to {user_settings.webhook_url} for spamcheck {spamcheck.id}")
    log_to_terminal("Webhook", "Info", f"Initiating webhook send to {user_settings.webhook_url} for spamcheck {spamcheck.id}")
    
    # Limit reports to a maximum of 100 to avoid overwhelming the webhook
    if len(reports_data) > 100:
        print(f"⚠️ Large number of reports ({len(reports_data)}), limiting to first 100")
        log_to_terminal("Webhook", "Warning", f"Large number of reports ({len(reports_data)}), limiting to first 100")
        reports_data = reports_data[:100]
    
    # Create a task but don't track the result directly - let it run independently
    asyncio.create_task(_send_webhook_task(user_settings, spamcheck, reports_data))
    
    log_to_terminal("Webhook", "Info", f"Webhook task started for spamcheck {spamcheck.id}")
    print(f"✅ Webhook task created for spamcheck {spamcheck.id}")
    
    # Return immediately - don't wait for webhook completion
    return

async def _send_webhook_task(user_settings, spamcheck, reports_data):
    """
    Internal task to handle webhook sending without blocking the main process
    """
    start_time = timezone.now()
    try:
        # Calculate overall results
        total_accounts = len(reports_data)
        if total_accounts == 0:
            print(f"⚠️ No accounts in reports_data for spamcheck {spamcheck.id}")
            log_to_terminal("Webhook", "Warning", f"No accounts in reports_data for spamcheck {spamcheck.id}")
            return
            
        print(f"📊 Preparing webhook data for {total_accounts} accounts")
        log_to_terminal("Webhook", "Info", f"Preparing webhook data for {total_accounts} accounts")
        
        # Process report statistics in a fast loop - avoid expensive calculations
        google_score_sum = 0
        outlook_score_sum = 0
        good_accounts = 0
        bounced_count = 0
        unique_replied_count = 0
        emails_sent_count = 0
        
        for report in reports_data:
            google_score_sum += float(report['google_pro_score'])
            outlook_score_sum += float(report['outlook_pro_score'])
            if report['is_good']:
                good_accounts += 1
            bounced_count += report.get('bounced_count', 0)
            unique_replied_count += report.get('unique_replied_count', 0) 
            emails_sent_count += report.get('emails_sent_count', 0)
        
        bad_accounts = total_accounts - good_accounts
        
        # Calculate averages
        avg_google_score = google_score_sum / total_accounts if total_accounts > 0 else 0
        avg_outlook_score = outlook_score_sum / total_accounts if total_accounts > 0 else 0
        
        # Create a comprehensive JSON encoder that handles all types we might encounter
        class WebhookJSONEncoder(DjangoJSONEncoder):
            """Custom JSON encoder that extends Django's encoder to handle additional types"""
            def default(self, obj):
                # Handle UUIDs
                if isinstance(obj, UUID):
                    return str(obj)
                # Handle Decimal types
                elif isinstance(obj, Decimal):
                    return float(obj)
                # Let Django's encoder handle datetime, date, time, timedelta, etc.
                try:
                    return super().default(obj)
                except TypeError:
                    # For anything else, convert to string as a last resort
                    return str(obj)
        
        # Create a JSON-serializable copy of spamcheck data
        spamcheck_data = {
            "id": spamcheck.id,
            "name": spamcheck.name,
            "status": spamcheck.status,
            "created_at": spamcheck.created_at,  # The encoder will handle this
            "updated_at": spamcheck.updated_at,  # The encoder will handle this
            "is_domain_based": spamcheck.is_domain_based,
            "subject": spamcheck.subject[:100] + "..." if len(spamcheck.subject) > 100 else spamcheck.subject,  # Truncate long texts
            "body": spamcheck.body[:200] + "..." if len(spamcheck.body) > 200 else spamcheck.body,  # Truncate long texts
            "conditions": spamcheck.conditions,
        }
        
        # Let the encoder handle all the report fields during JSON serialization
        # No need to manually convert each field
        
        # Build the payload
        payload = {
            "event": "spamcheck.completed",
            "spamcheck": spamcheck_data,
            "overall_results": {
                "total_accounts": total_accounts,
                "good_accounts": good_accounts,
                "bad_accounts": bad_accounts,
                "good_accounts_percentage": round((good_accounts / total_accounts) * 100, 2) if total_accounts > 0 else 0,
                "bad_accounts_percentage": round((bad_accounts / total_accounts) * 100, 2) if total_accounts > 0 else 0,
                "average_google_score": round(avg_google_score, 2),
                "average_outlook_score": round(avg_outlook_score, 2),
                "total_bounced": bounced_count,
                "total_unique_replies": unique_replied_count,
                "total_emails_sent": emails_sent_count,
            },
            "reports": reports_data  # Let the encoder handle all field types
        }
        
        # Serialize the payload with our comprehensive encoder
        try:
            payload_json = json.dumps(payload, cls=WebhookJSONEncoder)
        except Exception as e:
            # If serialization fails, log the error and try a more aggressive approach
            print(f"❌ Error serializing webhook JSON: {str(e)}")
            log_to_terminal("Webhook", "Error", f"Error serializing webhook JSON: {str(e)}")
            
            # Fallback: serialize everything manually
            payload_copy = {
                "event": "spamcheck.completed",
                "spamcheck": {
                    "id": str(spamcheck.id),
                    "name": str(spamcheck.name),
                    "status": str(spamcheck.status),
                    "created_at": spamcheck.created_at.isoformat() if hasattr(spamcheck.created_at, 'isoformat') else str(spamcheck.created_at),
                    "updated_at": spamcheck.updated_at.isoformat() if hasattr(spamcheck.updated_at, 'isoformat') else str(spamcheck.updated_at),
                    "is_domain_based": bool(spamcheck.is_domain_based),
                    "subject": str(spamcheck.subject)[:100] + "..." if len(str(spamcheck.subject)) > 100 else str(spamcheck.subject),
                    "body": str(spamcheck.body)[:200] + "..." if len(str(spamcheck.body)) > 200 else str(spamcheck.body),
                    "conditions": str(spamcheck.conditions),
                },
                "overall_results": {
                    "total_accounts": int(total_accounts),
                    "good_accounts": int(good_accounts),
                    "bad_accounts": int(bad_accounts),
                    "good_accounts_percentage": float(round((good_accounts / total_accounts) * 100, 2)) if total_accounts > 0 else 0.0,
                    "bad_accounts_percentage": float(round((bad_accounts / total_accounts) * 100, 2)) if total_accounts > 0 else 0.0,
                    "average_google_score": float(round(avg_google_score, 2)),
                    "average_outlook_score": float(round(avg_outlook_score, 2)),
                    "total_bounced": int(bounced_count),
                    "total_unique_replies": int(unique_replied_count),
                    "total_emails_sent": int(emails_sent_count),
                },
                "reports": []
            }
            
            # Manually convert every field in every report
            for report in reports_data:
                safe_report = {}
                for key, value in report.items():
                    # Convert each value to a JSON-safe type
                    if value is None:
                        safe_report[key] = None
                    elif isinstance(value, (int, float, bool, str)):
                        safe_report[key] = value
                    elif isinstance(value, (datetime, UUID, Decimal)):
                        safe_report[key] = str(value)
                    else:
                        safe_report[key] = str(value)
                payload_copy["reports"].append(safe_report)
            
            # Try again with the manually serialized payload
            payload_json = json.dumps(payload_copy)
            print("⚠️ Used fallback JSON serialization")
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "InboxAssure-Webhook/1.0"
        }
        
        # Set shorter timeout for fire-and-forget pattern
        timeout = 10  # 10 seconds timeout
        
        # Print webhook URL and payload size for debugging
        payload_size = len(payload_json)
        print(f"📡 Sending webhook to {user_settings.webhook_url}")
        print(f"📦 Payload size: {payload_size} bytes")
        log_to_terminal("Webhook", "Info", f"Sending webhook to {user_settings.webhook_url} with payload size {payload_size} bytes")
        
        processing_time = (timezone.now() - start_time).total_seconds()
        print(f"⏱️ Webhook preparation took: {processing_time:.2f} seconds")
        
        # Debug log a small sample of the payload
        try:
            print(f"📋 Sample of payload: {payload_json[:200]}...")
        except:
            print(f"📋 Could not print payload sample")

        # ============= SIMPLIFIED WEBHOOK SENDING =============
        # Use requests directly instead of aiohttp to ensure it completes
        print(f"🔄 Using synchronous requests directly")
        
        def send_sync_webhook():
            """Send webhook using synchronous requests"""
            try:
                post_start = datetime.now()
                print(f"⏱️ Starting synchronous POST to {user_settings.webhook_url}")
                
                response = requests.post(
                    user_settings.webhook_url,
                    data=payload_json,
                    headers=headers,
                    timeout=timeout
                )
                
                post_time = (datetime.now() - post_start).total_seconds()
                print(f"⏱️ POST completed in {post_time:.2f} seconds with status {response.status_code}")
                
                if response.status_code == 200:
                    print(f"✅ Webhook successfully sent with status 200")
                    log_to_terminal("Webhook", "Success", f"Webhook sent successfully with status 200")
                    return True
                else:
                    print(f"⚠️ Webhook received non-200 response: {response.status_code}")
                    try:
                        print(f"📄 Response: {response.text[:200]}")
                    except:
                        print("Could not read response")
                    return False
            except Exception as e:
                print(f"❌ Synchronous webhook failed: {str(e)}")
                log_to_terminal("Webhook", "Error", f"Synchronous webhook failed: {str(e)}")
                return False
                
        # Use the executor to run the synchronous function without blocking
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(None, send_sync_webhook)
        
        # Log final result
        if success:
            print(f"🚀 Webhook for spamcheck {spamcheck.id} completed successfully")
            log_to_terminal("Webhook", "Success", f"Webhook for spamcheck {spamcheck.id} completed successfully")
        else:
            print(f"❌ Webhook for spamcheck {spamcheck.id} failed")
            log_to_terminal("Webhook", "Error", f"Webhook for spamcheck {spamcheck.id} failed")
            
    except Exception as outer_error:
        print(f"❌ Outer error in webhook process: {str(outer_error)}")
        log_to_terminal("Webhook", "Error", f"Outer error in webhook process: {str(outer_error)}")
    
    # Total execution time
    total_time = (timezone.now() - start_time).total_seconds()
    print(f"⏱️ Total webhook process took {total_time:.2f} seconds")
    log_to_terminal("Webhook", "Info", f"Total webhook process took {total_time:.2f} seconds")

def send_test_webhook_sync(webhook_url):
    """
    Send test webhook data to verify webhook configuration (synchronous version)
    
    Args:
        webhook_url: URL to send the test webhook
        
    Returns:
        dict: Result of the webhook test with status and message
    """
    try:
        # Get current time in ISO format for JSON serialization
        current_time = timezone.now()
        
        # Create mock spamcheck data
        mock_data = {
            "event": "spamcheck.test",
            "spamcheck": {
                "id": 12345,
                "name": "Test Spamcheck",
                "status": "completed",
                "created_at": current_time,
                "updated_at": current_time,
                "is_domain_based": True,
                "subject": "Test Subject",
                "body": "Test Body",
                "conditions": "google>=0.5 and outlook>=0.5 sending=25/3",
            },
            "overall_results": {
                "total_accounts": 5,
                "good_accounts": 3,
                "bad_accounts": 2,
                "good_accounts_percentage": 60.0,
                "bad_accounts_percentage": 40.0,
                "average_google_score": 0.65,
                "average_outlook_score": 0.58,
                "total_bounced": 2,
                "total_unique_replies": 10,
                "total_emails_sent": 500,
            },
            "reports": [
                {
                    "id": UUID("11111111-1111-1111-1111-111111111111"),
                    "email_account": "test1@example.com",
                    "google_pro_score": Decimal("0.8"),
                    "outlook_pro_score": Decimal("0.7"),
                    "report_link": "https://app.emailguard.io/inbox-placement-tests/test-tag-1",
                    "is_good": True,
                    "sending_limit": 25,
                    "tags_list": "tag1,tag2",
                    "workspace_name": "Workspace 1",
                    "bounced_count": 0,
                    "unique_replied_count": 5,
                    "emails_sent_count": 200,
                    "created_at": current_time
                },
                {
                    "id": UUID("22222222-2222-2222-2222-222222222222"),
                    "email_account": "test2@example.com",
                    "google_pro_score": Decimal("0.4"),
                    "outlook_pro_score": Decimal("0.3"),
                    "report_link": "https://app.emailguard.io/inbox-placement-tests/test-tag-2",
                    "is_good": False,
                    "sending_limit": 3,
                    "tags_list": "tag1,tag3",
                    "workspace_name": "Workspace 1",
                    "bounced_count": 2,
                    "unique_replied_count": 0,
                    "emails_sent_count": 50,
                    "created_at": current_time
                }
            ]
        }
        
        # Use the same encoder as the main webhook function
        class WebhookJSONEncoder(DjangoJSONEncoder):
            """Custom JSON encoder that extends Django's encoder to handle additional types"""
            def default(self, obj):
                # Handle UUIDs
                if isinstance(obj, UUID):
                    return str(obj)
                # Handle Decimal types
                elif isinstance(obj, Decimal):
                    return float(obj)
                # Let Django's encoder handle datetime, date, time, timedelta, etc.
                try:
                    return super().default(obj)
                except TypeError:
                    # For anything else, convert to string as a last resort
                    return str(obj)
        
        # Convert mock data to JSON with the robust encoder
        mock_data_json = json.dumps(mock_data, cls=WebhookJSONEncoder)
        print(f"📋 Test webhook size: {len(mock_data_json)} bytes")
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "InboxAssure-Webhook-Test/1.0"
        }
        
        # Use a short timeout for the test - using normal requests library (not async)
        print(f"📤 Sending test webhook to {webhook_url}")
        response = requests.post(
            webhook_url,
            json=json.loads(mock_data_json),  # Ensure proper JSON format
            headers=headers,
            timeout=10
        )
        
        print(f"📥 Received status {response.status_code}")
        
        if response.status_code == 200:
            return {
                "success": True,
                "status_code": response.status_code,
                "message": "Webhook test sent successfully and received 200 OK response."
            }
        else:
            # Try to get response body for more detailed error
            try:
                response_text = response.text
                print(f"📄 Response: {response_text[:200]}")
            except:
                response_text = "Unable to read response body"
            
            return {
                "success": False,
                "status_code": response.status_code, 
                "message": f"Webhook sent but received non-200 response: {response.status_code}",
                "response": response_text[:500]  # Limit response size
            }
                
    except requests.exceptions.Timeout:
        return {
            "success": False,
            "message": "Webhook request timed out after 10 seconds."
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Error sending test webhook: {str(e)}"
        } 