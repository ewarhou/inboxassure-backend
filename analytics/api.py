from datetime import datetime
from typing import List, Optional
from ninja import Router, Schema
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from django.db import connection
from authentication.authorization import AuthBearer
from spamcheck.services.instantly import InstantlyService
from settings.api import log_to_terminal

router = Router(tags=["Analytics"])

class OrganizationSummaryResponse(Schema):
    """Response model for organization dashboard summary metrics"""
    organization_id: str
    organization_name: str
    checked_accounts: int
    at_risk_accounts: int
    protected_accounts: int
    spam_emails_count: int
    inbox_emails_count: int
    spam_emails_percentage: float
    inbox_emails_percentage: float
    overall_deliverability: float
    last_check_date: str

    class Config:
        schema_extra = {
            "description": "Organization dashboard summary metrics",
            "fields": {
                "organization_id": "Organization's workspace UUID",
                "organization_name": "Organization name",
                "checked_accounts": "Total number of checked accounts",
                "at_risk_accounts": "Number of accounts at risk",
                "protected_accounts": "Number of protected accounts",
                "spam_emails_count": "Estimated number of emails going to spam based on sending limit",
                "inbox_emails_count": "Estimated number of emails going to inbox based on sending limit",
                "spam_emails_percentage": "Percentage of emails going to spam",
                "inbox_emails_percentage": "Percentage of emails going to inbox",
                "overall_deliverability": "Overall deliverability score",
                "last_check_date": "ISO formatted date of the last check"
            }
        }

class SendingPowerData(Schema):
    """Data model for sending power entry"""
    date: str
    workspace_name: str
    sending_power: int

class SendingPowerResponse(Schema):
    """Response model for sending power endpoint"""
    data: List[SendingPowerData]

    class Config:
        schema_extra = {
            "description": "Daily sending power per workspace",
            "example": {
                "data": [
                    {
                        "date": "2024-02-18",
                        "workspace_name": "Example Workspace",
                        "sending_power": 1250
                    }
                ]
            }
        }

@router.get(
    "/dashboard/sending-power",
    response=SendingPowerResponse,
    auth=AuthBearer(),
    summary="Daily Sending Power",
    description="Get daily sending power (good accounts × sending limit) per workspace within a date range"
)
def get_sending_power(
    request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get daily sending power metrics per workspace
    
    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    user = request.auth
    
    # Set default date range if not provided (last 30 days)
    if not end_date:
        end_date = timezone.now().date().isoformat()
    if not start_date:
        start_date = (timezone.now() - timezone.timedelta(days=30)).date().isoformat()
    
    with connection.cursor() as cursor:
        query = """
            WITH daily_reports AS (
                SELECT 
                    usr.*,
                    ui.instantly_organization_name,
                    DATE(usr.created_at) as report_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY usr.email_account, usr.instantly_workspace_uuid, DATE(usr.created_at)
                        ORDER BY usr.created_at DESC
                    ) as rn
                FROM user_spamcheck_reports usr
                JOIN user_instantly ui ON usr.organization_id = ui.id
                WHERE ui.user_id = %s
                AND DATE(usr.created_at) BETWEEN %s AND %s
            )
            SELECT 
                report_date,
                instantly_organization_name,
                COUNT(CASE WHEN is_good = true THEN 1 END) * COALESCE(MAX(sending_limit), 25) as daily_power
            FROM daily_reports
            WHERE rn = 1
            GROUP BY report_date, instantly_organization_name
            ORDER BY report_date DESC, instantly_organization_name
        """
        
        cursor.execute(query, [user.id, start_date, end_date])
        results = cursor.fetchall()
        
        if not results:
            return {"data": []}
        
        data = []
        for result in results:
            report_date, workspace_name, sending_power = result
            data.append({
                "date": report_date.isoformat(),
                "workspace_name": workspace_name,
                "sending_power": sending_power or 0
            })
        
        return {"data": data}

@router.get(
    "/dashboard/summary", 
    response=List[OrganizationSummaryResponse], 
    auth=AuthBearer(),
    summary="Dashboard Summary Per Organization",
    description="Get summary metrics for the dashboard per organization including account stats and email delivery predictions"
)
def get_dashboard_summary(request):
    """Get dashboard summary metrics per organization using only the latest report for each account"""
    from django.contrib.auth import get_user_model
    User = get_user_model()
    
    # Get user from auth - request.auth is already the User object
    user = request.auth
    
    # Use raw SQL for better performance
    with connection.cursor() as cursor:
        query = """
        WITH latest_reports AS (
            SELECT 
                usr.*,
                ui.instantly_organization_name,
                ui.instantly_organization_id,
                ROW_NUMBER() OVER (
                    PARTITION BY usr.email_account, usr.instantly_workspace_uuid 
                    ORDER BY usr.created_at DESC
                ) as rn
            FROM user_spamcheck_reports usr
            JOIN user_instantly ui ON usr.organization_id = ui.id
            WHERE ui.user_id = %s
        )
        SELECT 
            instantly_workspace_uuid as org_id,
            instantly_organization_name as org_name,
            COUNT(*) as checked,
            COUNT(CASE WHEN is_good = false THEN 1 END) as at_risk,
            COUNT(CASE WHEN is_good = true THEN 1 END) as protected,
            COALESCE(AVG(google_pro_score), 0) as avg_google,
            COALESCE(AVG(outlook_pro_score), 0) as avg_outlook,
            MAX(created_at) as last_check,
            COALESCE(MAX(sending_limit), 25) as sending_limit
        FROM latest_reports
        WHERE rn = 1
        GROUP BY instantly_workspace_uuid, instantly_organization_name
        ORDER BY instantly_organization_name
        """
        cursor.execute(query, [user.id])
        results = cursor.fetchall()
        
        if not results:
            return []
        
        summaries = []
        for result in results:
            (org_id, org_name, checked, at_risk, protected, avg_google, avg_outlook, 
             last_check, sending_limit) = result
            
            # Calculate email counts using the same sending limit for both
            spam_emails = at_risk * sending_limit
            inbox_emails = protected * sending_limit
            total_emails = spam_emails + inbox_emails
            
            # Calculate percentages
            spam_percentage = round((spam_emails / total_emails * 100), 2) if total_emails > 0 else 0
            inbox_percentage = round((inbox_emails / total_emails * 100), 2) if total_emails > 0 else 0
            
            overall_deliverability = round(((float(avg_google) + float(avg_outlook)) / 2) * 25, 2)
            
            summaries.append({
                "organization_id": org_id,
                "organization_name": org_name,
                "checked_accounts": checked or 0,
                "at_risk_accounts": at_risk or 0,
                "protected_accounts": protected or 0,
                "spam_emails_count": spam_emails,
                "inbox_emails_count": inbox_emails,
                "spam_emails_percentage": spam_percentage,
                "inbox_emails_percentage": inbox_percentage,
                "overall_deliverability": overall_deliverability,
                "last_check_date": last_check.isoformat() if last_check else None
            })
        
        return summaries

class ProviderPerformanceData(Schema):
    """Data model for provider performance metrics"""
    organization: str
    provider: str
    start_date: str
    end_date: str
    total_accounts: int
    reply_rate: Optional[float] = None
    bounce_rate: Optional[float] = None
    google_score: float
    outlook_score: float
    overall_score: float
    sending_power: int

    class Config:
        schema_extra = {
            "description": "Provider performance metrics",
            "example": {
                "organization": "Example Org",
                "provider": "Gmail",
                "start_date": "2024-02-01",
                "end_date": "2024-02-18",
                "total_accounts": 125,
                "reply_rate": None,
                "bounce_rate": None,
                "google_score": 92.3,
                "outlook_score": 88.7,
                "overall_score": 90.5,
                "sending_power": 3125
            }
        }

class ProviderPerformanceResponse(Schema):
    """Response model for provider performance endpoint"""
    data: List[ProviderPerformanceData]

@router.get(
    "/dashboard/provider-performance",
    response=ProviderPerformanceResponse,
    auth=AuthBearer(),
    summary="Provider Performance Metrics",
    description="Get performance metrics per provider including daily aggregated scores and sending power"
)
def get_provider_performance(
    request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    Get provider performance metrics within a date range.
    Aggregates data daily per provider and averages over the period.
    
    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    user = request.auth
    log_to_terminal("Performance", "Request", f"Request for {user.email}")
    
    if not end_date:
        end_date = timezone.now().date().isoformat()
    if not start_date:
        start_date = (timezone.now() - timezone.timedelta(days=30)).date().isoformat()
    
    log_to_terminal("Performance", "DateRange", f"Range: {start_date} to {end_date}")
    
    # Fetch provider tags from Instantly
    try:
        instantly = InstantlyService(user)
        tags = instantly.get_tags()
        # Build mapping for provider tags only (names starting with "p.")
        provider_tags = {
            tag['id']: tag['name'][2:] for tag in tags if tag['name'].startswith('p.')
        }
        log_to_terminal("Performance", "Tags", f"Found {len(provider_tags)} provider tags")
    except Exception as e:
        log_to_terminal("Performance", "Error", f"Error fetching tags: {str(e)}")
        provider_tags = {}
    
    # Get all reports within date range
    with connection.cursor() as cursor:
        query = """
            SELECT 
                ui.instantly_organization_name,
                usr.tags_uuid_list,
                usr.google_pro_score,
                usr.outlook_pro_score,
                usr.email_account,
                DATE(usr.created_at) as report_date,
                usr.sending_limit,
                usr.is_good
            FROM user_spamcheck_reports usr
            JOIN user_instantly ui ON usr.organization_id = ui.id
            WHERE ui.user_id = %s
              AND DATE(usr.created_at) BETWEEN %s AND %s
              AND usr.tags_uuid_list IS NOT NULL
            ORDER BY usr.created_at DESC
        """
        cursor.execute(query, [user.id, start_date, end_date])
        rows = cursor.fetchall()
        log_to_terminal("Performance", "SQL", f"Found {len(rows)} rows")
    
    # Create a nested defaultdict for daily stats
    from collections import defaultdict
    from datetime import datetime, timedelta
    
    # Helper function to create a new stats dictionary
    def new_stats_dict():
        return {
            'accounts': set(),  # Use set to count unique accounts
            'good_accounts': 0,
            'google_sum': 0.0,
            'outlook_sum': 0.0,
            'sending_power': 0
        }
    
    # Structure: {org_name: {provider: {date: stats}}}
    daily_stats = defaultdict(lambda: defaultdict(lambda: defaultdict(new_stats_dict)))
    
    # Process each row and aggregate by day
    for row in rows:
        (org_name, tags_uuid_list, google_score, outlook_score, 
         email_account, report_date, sending_limit, is_good) = row
        
        # Default sending limit if not specified
        sending_limit = sending_limit or 25
        
        try:
            account_tags = [tag.strip() for tag in tags_uuid_list.split(',') if tag.strip()]
            # Get unique provider tags for this account
            account_provider_tags = set(tag for tag in account_tags if tag in provider_tags)
            
            # Process each provider tag
            for tag in account_provider_tags:
                provider_name = provider_tags[tag]
                stats = daily_stats[org_name][provider_name][report_date]
                
                # Update daily stats
                if email_account not in stats['accounts']:
                    stats['accounts'].add(email_account)
                    # Only add scores for new accounts to avoid double counting
                    stats['google_sum'] += float(google_score or 0.0)
                    stats['outlook_sum'] += float(outlook_score or 0.0)
                    if is_good:
                        stats['good_accounts'] += 1
                        stats['sending_power'] += sending_limit
                
        except Exception as e:
            log_to_terminal("Performance", "Error", 
                          f"Error processing account {email_account}: {str(e)}")
            continue
    
    # Calculate period averages
    data = []
    start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
    days_in_period = (end_dt - start_dt).days + 1
    
    for org_name, providers in daily_stats.items():
        for provider_name, daily_data in providers.items():
            # Initialize period totals
            period_accounts = set()  # Track unique accounts across period
            daily_scores = {  # Track daily averages
                'google': [],
                'outlook': [],
                'sending_power': []
            }
            
            # Iterate through each day in the period
            current_dt = start_dt
            while current_dt <= end_dt:
                if current_dt in daily_data:
                    stats = daily_data[current_dt]
                    period_accounts.update(stats['accounts'])
                    
                    # Calculate daily averages
                    accounts_count = len(stats['accounts'])
                    if accounts_count > 0:
                        # Scores are already summed correctly per day, just divide by accounts
                        google_score = stats['google_sum'] / accounts_count
                        outlook_score = stats['outlook_sum'] / accounts_count
                        # Ensure scores are within valid range (0-4)
                        daily_scores['google'].append(min(4.0, max(0.0, google_score)))
                        daily_scores['outlook'].append(min(4.0, max(0.0, outlook_score)))
                        daily_scores['sending_power'].append(stats['sending_power'])
                
                current_dt += timedelta(days=1)
            
            # Calculate period averages
            if daily_scores['google']:  # If we have any data
                avg_google = round(sum(daily_scores['google']) / len(daily_scores['google']), 2)
                avg_outlook = round(sum(daily_scores['outlook']) / len(daily_scores['outlook']), 2)
                avg_sending_power = round(sum(daily_scores['sending_power']) / len(daily_scores['sending_power']))
                overall_score = round((avg_google + avg_outlook) / 2, 2)
                
                data.append({
                    "organization": org_name,
                    "provider": provider_name,
                    "start_date": start_date,
                    "end_date": end_date,
                    "total_accounts": len(period_accounts),
                    "reply_rate": None,
                    "bounce_rate": None,
                    "google_score": avg_google,  # Already capped at 4.0 in daily calculations
                    "outlook_score": avg_outlook,  # Already capped at 4.0 in daily calculations
                    "overall_score": overall_score,
                    "sending_power": avg_sending_power
                })
    
    log_to_terminal("Performance", "Output", f"Returning {len(data)} provider entries")
    return {"data": data} 