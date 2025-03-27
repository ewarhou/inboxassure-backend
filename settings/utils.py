import aiohttp
import asyncio
import json
from django.core.serializers.json import DjangoJSONEncoder
from settings.api import log_to_terminal
from django.db.models import Avg, Count, Q

async def send_webhook(user_settings, spamcheck, reports_data):
    """
    Send webhook notification for completed spamcheck without waiting for response
    
    Args:
        user_settings: UserSettings instance
        spamcheck: UserSpamcheckBison instance
        reports_data: List of UserSpamcheckBisonReport data
    """
    if not user_settings or not user_settings.webhook_url:
        return
        
    try:
        # Calculate overall results
        total_accounts = len(reports_data)
        if total_accounts == 0:
            return
            
        google_scores = [report['google_pro_score'] for report in reports_data]
        outlook_scores = [report['outlook_pro_score'] for report in reports_data]
        good_accounts = sum(1 for report in reports_data if report['is_good'])
        bad_accounts = total_accounts - good_accounts
        
        # Calculate averages
        avg_google_score = sum(google_scores) / total_accounts
        avg_outlook_score = sum(outlook_scores) / total_accounts
        
        payload = {
            "event": "spamcheck.completed",
            "spamcheck": {
                "id": spamcheck.id,
                "name": spamcheck.name,
                "status": spamcheck.status,
                "created_at": spamcheck.created_at,
                "updated_at": spamcheck.updated_at,
                "is_domain_based": spamcheck.is_domain_based,
                "subject": spamcheck.subject,
                "body": spamcheck.body,
                "conditions": spamcheck.conditions,
            },
            "overall_results": {
                "total_accounts": total_accounts,
                "good_accounts": good_accounts,
                "bad_accounts": bad_accounts,
                "good_accounts_percentage": round((good_accounts / total_accounts) * 100, 2),
                "bad_accounts_percentage": round((bad_accounts / total_accounts) * 100, 2),
                "average_google_score": round(avg_google_score, 2),
                "average_outlook_score": round(avg_outlook_score, 2),
                "total_bounced": sum(report['bounced_count'] for report in reports_data),
                "total_unique_replies": sum(report['unique_replied_count'] for report in reports_data),
                "total_emails_sent": sum(report['emails_sent_count'] for report in reports_data),
            },
            "reports": reports_data
        }
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "InboxAssure-Webhook/1.0"
        }
        
        # Fire and forget - don't wait for response
        async def fire_webhook():
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        user_settings.webhook_url,
                        json=payload,
                        headers=headers,
                        timeout=10
                    )
            except:
                pass  # Ignore any errors
        
        # Schedule the webhook without waiting
        asyncio.create_task(fire_webhook())
        log_to_terminal("Webhook", "Sent", f"Webhook fired for spamcheck {spamcheck.id}")
                    
    except Exception as e:
        log_to_terminal("Webhook", "Error", f"Error preparing webhook for spamcheck {spamcheck.id}: {str(e)}") 