from datetime import datetime
from typing import List, Optional, Dict, Any
from ninja import Router, Schema, Query
from ninja.pagination import paginate
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from django.db import connection
from authentication.authorization import AuthBearer
from spamcheck.services.instantly import InstantlyService
from settings.api import log_to_terminal
import requests
from settings.models import UserBison, UserInstantly
from pydantic import BaseModel, Field
from decimal import Decimal
import json
import time
import re
from django.conf import settings

router = Router(tags=["Analytics"])

class PaginationSchema(Schema):
    page: int = 1
    per_page: int = 10

class BisonCampaignResponse(BaseModel):
    id: int
    name: str
    connectedEmails: int = Field(..., description="Count of emails connected to this campaign")
    sendsPerAccount: int = Field(..., description="Number of sends per account")
    googleScore: float = Field(..., description="Google deliverability score (0-100)")
    outlookScore: float = Field(..., description="Outlook deliverability score (0-100)")
    maxDailySends: int = Field(..., description="Maximum daily sends")

class BisonOrganizationSummaryResponse(Schema):
    """Response model for Bison organization dashboard summary metrics"""
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
    last_check_date: Optional[str] = ""

    class Config:
        schema_extra = {
            "description": "Bison organization dashboard summary metrics",
            "fields": {
                "organization_id": "Bison organization ID",
                "organization_name": "Bison workspace name",
                "checked_accounts": "Number of accounts that exist in both Bison and reports",
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

class BisonSendingPowerData(Schema):
    """Data model for Bison sending power entry"""
    date: str
    workspace_name: str
    sending_power: int

class BisonSendingPowerResponse(Schema):
    """Response model for Bison sending power endpoint"""
    data: List[BisonSendingPowerData]

    class Config:
        schema_extra = {
            "description": "Daily sending power per Bison workspace",
            "example": {
                "data": [
                    {
                        "date": "2024-02-18",
                        "workspace_name": "Karpos",
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
    "/dashboard/sending-power-bison",
    response=BisonSendingPowerResponse,
    auth=AuthBearer(),
    summary="Daily Sending Power for Bison",
    description="Get daily sending power (good accounts × sending limit) per Bison workspace within a date range"
)
def get_bison_sending_power(
    request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """Get daily sending power metrics per Bison workspace
    
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
                    ubr.*,
                    ub.bison_organization_name,
                    DATE(ubr.created_at) as report_date,
                    ROW_NUMBER() OVER (
                        PARTITION BY ubr.email_account, ubr.bison_organization_id, DATE(ubr.created_at)
                        ORDER BY ubr.created_at DESC
                    ) as rn
                FROM user_spamcheck_bison_reports ubr
                JOIN user_bison ub ON ubr.bison_organization_id = ub.id
                WHERE ub.user_id = %s
                AND DATE(ubr.created_at) BETWEEN %s AND %s
            )
            SELECT 
                report_date,
                bison_organization_name,
                COUNT(CASE WHEN is_good = true THEN 1 END) * COALESCE(MAX(sending_limit), 25) as daily_power
            FROM daily_reports
            WHERE rn = 1
            GROUP BY report_date, bison_organization_name
            ORDER BY report_date DESC, bison_organization_name
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

class BisonProviderPerformanceData(Schema):
    """Data model for Bison provider performance metrics"""
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
            "description": "Bison provider performance metrics",
            "example": {
                "organization": "Karpos",
                "provider": "Gmail",
                "start_date": "2024-02-01",
                "end_date": "2024-02-18",
                "total_accounts": 125,
                "reply_rate": None,
                "bounce_rate": None,
                "google_score": 0.92,
                "outlook_score": 0.88,
                "overall_score": 0.90,
                "sending_power": 3125
            }
        }

class BisonProviderPerformanceResponse(Schema):
    """Response model for Bison provider performance endpoint"""
    data: List[BisonProviderPerformanceData]

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

@router.get(
    "/dashboard/provider-performance-bison",
    response=BisonProviderPerformanceResponse,
    auth=AuthBearer(),
    summary="Bison Provider Performance Metrics",
    description="Get performance metrics per provider for Bison accounts including daily aggregated scores and sending power"
)
def get_bison_provider_performance(
    request,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    """
    Get Bison provider performance metrics within a date range.
    Aggregates data daily per provider and averages over the period.
    
    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    user = request.auth
    log_to_terminal("BisonPerformance", "Request", f"Request for {user.email}")
    
    if not end_date:
        end_date = timezone.now().date().isoformat()
    if not start_date:
        start_date = (timezone.now() - timezone.timedelta(days=30)).date().isoformat()
    
    log_to_terminal("BisonPerformance", "DateRange", f"Range: {start_date} to {end_date}")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    if not bison_orgs:
        log_to_terminal("BisonPerformance", "Warning", "No active Bison organizations found")
        return {"data": []}
    
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
    
    # Process each Bison organization
    for org in bison_orgs:
        log_to_terminal("BisonPerformance", "Processing", f"Organization: {org.bison_organization_name}")
        
        try:
            # 1. Get ALL accounts from Bison API by paginating until we have everything
            api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
            
            all_bison_accounts = []
            current_page = 1
            per_page = 100  # Use a larger page size for efficiency
            
            while True:
                response = requests.get(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {org.bison_organization_api_key}",
                        "Content-Type": "application/json"
                    },
                    params={
                        "page": current_page,
                        "per_page": per_page
                    }
                )
                
                if response.status_code != 200:
                    log_to_terminal("BisonPerformance", "Error", f"API Error: {response.text}")
                    break
                    
                data = response.json()
                accounts = data.get('data', [])
                all_bison_accounts.extend(accounts)
                
                # Check if we've reached the last page
                total_pages = data.get('meta', {}).get('last_page', 1)
                if current_page >= total_pages:
                    break
                    
                current_page += 1
            
            log_to_terminal("BisonPerformance", "Accounts", f"Found {len(all_bison_accounts)} accounts for {org.bison_organization_name}")
            
            # Deduplicate accounts by email to handle Bison API duplicate issue
            unique_emails = {}
            for account in all_bison_accounts:
                email = account.get('email')
                if email and email not in unique_emails:
                    unique_emails[email] = account
            
            bison_emails = list(unique_emails.keys())
            
            if not bison_emails:
                log_to_terminal("BisonPerformance", "Warning", f"No valid email addresses found for {org.bison_organization_name}")
                continue
            
            # Create a mapping of provider tags
            # In Bison, we'll use the tags directly from the accounts
            provider_tags = {}
            for email, account in unique_emails.items():
                tags = account.get('tags', [])
                for tag in tags:
                    tag_name = tag.get('name', '')
                    # Similar to Instantly, we'll look for provider tags that start with "p."
                    if tag_name.startswith('p.'):
                        provider_tags[email] = tag_name[2:]  # Remove the "p." prefix
            
            log_to_terminal("BisonPerformance", "Tags", f"Found {len(provider_tags)} accounts with provider tags")
            
            # 2. Get reports for these accounts
            with connection.cursor() as cursor:
                # Process emails in chunks to avoid too many SQL parameters
                chunk_size = 500
                email_chunks = [bison_emails[i:i + chunk_size] for i in range(0, len(bison_emails), chunk_size)]
                
                for chunk in email_chunks:
                    placeholders = ','.join(['%s'] * len(chunk))
                    query = f"""
                    SELECT 
                        ubr.email_account,
                        ubr.google_pro_score,
                        ubr.outlook_pro_score,
                        ubr.is_good,
                        ubr.sending_limit,
                        DATE(ubr.created_at) as report_date
                    FROM user_spamcheck_bison_reports ubr
                    WHERE ubr.bison_organization_id = %s
                    AND ubr.email_account IN ({placeholders})
                    AND DATE(ubr.created_at) BETWEEN %s AND %s
                    ORDER BY ubr.created_at DESC
                    """
                    cursor.execute(query, [org.id] + chunk + [start_date, end_date])
                    rows = cursor.fetchall()
                    
                    # Process each row and aggregate by day and provider
                    for row in rows:
                        email_account, google_score, outlook_score, is_good, sending_limit, report_date = row
                        
                        # Skip if this account doesn't have a provider tag
                        if email_account not in provider_tags:
                            continue
                        
                        provider_name = provider_tags[email_account]
                        sending_limit = sending_limit or 25  # Default sending limit if not specified
                        
                        # Update daily stats
                        stats = daily_stats[org.bison_organization_name][provider_name][report_date]
                        
                        if email_account not in stats['accounts']:
                            stats['accounts'].add(email_account)
                            # Only add scores for new accounts to avoid double counting
                            stats['google_sum'] += float(google_score or 0.0)
                            stats['outlook_sum'] += float(outlook_score or 0.0)
                            if is_good:
                                stats['good_accounts'] += 1
                                stats['sending_power'] += sending_limit
            
        except Exception as e:
            log_to_terminal("BisonPerformance", "Error", f"Error processing org {org.bison_organization_name}: {str(e)}")
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
                        # Ensure scores are within valid range (0-1 for Bison)
                        daily_scores['google'].append(min(1.0, max(0.0, google_score)))
                        daily_scores['outlook'].append(min(1.0, max(0.0, outlook_score)))
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
                    "google_score": avg_google,
                    "outlook_score": avg_outlook,
                    "overall_score": overall_score,
                    "sending_power": avg_sending_power
                })
    
    log_to_terminal("BisonPerformance", "Output", f"Returning {len(data)} provider entries")
    return {"data": data}

@router.get(
    "/dashboard/summary-bison", 
    response=List[BisonOrganizationSummaryResponse], 
    auth=AuthBearer(),
    summary="Bison Dashboard Summary Per Organization",
    description="Get summary metrics for the dashboard per Bison organization including account stats and email delivery predictions"
)
def get_bison_dashboard_summary(request):
    """Get dashboard summary metrics per Bison organization using only the latest report for each account"""
    from django.contrib.auth import get_user_model
    from settings.models import UserBison
    from spamcheck.models import UserSpamcheckBisonReport
    
    User = get_user_model()
    user = request.auth
    
    print("\n=== DEBUG: Bison Dashboard Summary Start ===")
    print(f"User ID: {user.id}")
    print(f"User Email: {user.email}")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    print(f"\nFound Bison orgs: {bison_orgs.count()}")
    for org in bison_orgs:
        print(f"- Org: {org.bison_organization_name} (ID: {org.id})")
    
    if not bison_orgs:
        print("No active Bison organizations found")
        return []
    
    summaries = []
    
    for org in bison_orgs:
        try:
            print(f"\nProcessing org: {org.bison_organization_name}")
            print(f"Base URL: {org.base_url}")
            
            # 1. Get ALL accounts from Bison API by paginating until we have everything
            api_url = f"{org.base_url.rstrip('/')}/api/sender-emails"
            print(f"Calling Bison API: {api_url}")
            
            all_bison_accounts = []
            current_page = 1
            per_page = 100  # Use a larger page size for efficiency
            
            while True:
                response = requests.get(
                    api_url,
                    headers={
                        "Authorization": f"Bearer {org.bison_organization_api_key}",
                        "Content-Type": "application/json"
                    },
                    params={
                        "page": current_page,
                        "per_page": per_page
                    }
                )
                
                print(f"API Response Status for page {current_page}: {response.status_code}")
                
                if response.status_code != 200:
                    print(f"API Error: {response.text}")
                    break
                    
                data = response.json()
                accounts = data.get('data', [])
                all_bison_accounts.extend(accounts)
                
                # Check if we've reached the last page
                total_pages = data.get('meta', {}).get('last_page', 1)
                if current_page >= total_pages:
                    break
                    
                current_page += 1
            
            print(f"Found total {len(all_bison_accounts)} accounts in Bison")
            
            # Deduplicate accounts by email to handle Bison API duplicate issue
            unique_emails = {}
            for account in all_bison_accounts:
                email = account.get('email')
                if email and email not in unique_emails:
                    unique_emails[email] = account
            
            bison_emails = list(unique_emails.keys())
            print(f"Extracted {len(bison_emails)} unique email addresses")
            
            if not bison_emails:
                print("No valid email addresses found")
                continue
            
            # 2. Get latest reports for these accounts using raw SQL with proper email list handling
            print("\nQuerying reports from database...")
            with connection.cursor() as cursor:
                # Process emails in chunks to avoid too many SQL parameters
                chunk_size = 500
                email_chunks = [bison_emails[i:i + chunk_size] for i in range(0, len(bison_emails), chunk_size)]
                
                all_results = []
                for chunk in email_chunks:
                    placeholders = ','.join(['%s'] * len(chunk))
                    query = f"""
                    WITH latest_reports AS (
                        SELECT 
                            *,
                            ROW_NUMBER() OVER (
                                PARTITION BY email_account 
                                ORDER BY created_at DESC
                            ) as rn
                        FROM user_spamcheck_bison_reports
                        WHERE bison_organization_id = %s
                        AND email_account IN ({placeholders})
                    )
                    SELECT 
                        COUNT(DISTINCT email_account) as checked,
                        COUNT(CASE WHEN is_good = false THEN 1 END) as at_risk,
                        COUNT(CASE WHEN is_good = true THEN 1 END) as protected,
                        COALESCE(AVG(google_pro_score), 0) as avg_google,
                        COALESCE(AVG(outlook_pro_score), 0) as avg_outlook,
                        MAX(created_at) as last_check,
                        COALESCE(MAX(sending_limit), 25) as sending_limit
                    FROM latest_reports
                    WHERE rn = 1
                    """
                    print(f"Executing query with org_id={org.id} and {len(chunk)} emails")
                    cursor.execute(query, [org.id] + chunk)
                    result = cursor.fetchone()
                    if result and any(result):  # If we got any non-null results
                        all_results.append(result)
                
                if not all_results:
                    print("No reports found in database")
                    continue
                
                # Aggregate results from all chunks
                checked = sum(r[0] for r in all_results)
                at_risk = sum(r[1] for r in all_results)
                protected = sum(r[2] for r in all_results)
                avg_google = sum(r[3] * r[0] for r in all_results) / sum(r[0] for r in all_results) if any(r[0] for r in all_results) else 0
                avg_outlook = sum(r[4] * r[0] for r in all_results) / sum(r[0] for r in all_results) if any(r[0] for r in all_results) else 0
                last_check = max(r[5] for r in all_results) if any(r[5] for r in all_results) else None
                sending_limit = max(r[6] for r in all_results) if all_results else 25
                
                print(f"\nAggregated Metrics:")
                print(f"- Checked accounts: {checked}")
                print(f"- At risk: {at_risk}")
                print(f"- Protected: {protected}")
                print(f"- Avg Google score: {avg_google}")
                print(f"- Avg Outlook score: {avg_outlook}")
                print(f"- Sending limit: {sending_limit}")
                
                # Calculate email counts using sending limit
                spam_emails = at_risk * sending_limit if at_risk else 0
                inbox_emails = protected * sending_limit if protected else 0
                total_emails = spam_emails + inbox_emails
                
                # Calculate percentages
                spam_percentage = round((spam_emails / total_emails * 100), 2) if total_emails > 0 else 0
                inbox_percentage = round((inbox_emails / total_emails * 100), 2) if total_emails > 0 else 0
                
                # Calculate overall deliverability (average of Google and Outlook scores)
                overall_deliverability = round(((float(avg_google) + float(avg_outlook)) / 2) * 100, 2)
                
                print(f"\nCalculated metrics:")
                print(f"- Spam emails: {spam_emails}")
                print(f"- Inbox emails: {inbox_emails}")
                print(f"- Spam %: {spam_percentage}")
                print(f"- Inbox %: {inbox_percentage}")
                print(f"- Overall deliverability: {overall_deliverability}")
                
                summaries.append({
                    "organization_id": str(org.id),
                    "organization_name": org.bison_organization_name,
                    "checked_accounts": checked or 0,
                    "at_risk_accounts": at_risk or 0,
                    "protected_accounts": protected or 0,
                    "spam_emails_count": spam_emails,
                    "inbox_emails_count": inbox_emails,
                    "spam_emails_percentage": spam_percentage,
                    "inbox_emails_percentage": inbox_percentage,
                    "overall_deliverability": overall_deliverability,
                    "last_check_date": last_check.isoformat() if last_check else ""
                })
                print("\nAdded summary to response")
                
        except Exception as e:
            print(f"\nError processing org {org.bison_organization_name}: {str(e)}")
            continue
    
    print(f"\nFinal summaries count: {len(summaries)}")
    return summaries

class BisonOrganizationCampaignsResponse(BaseModel):
    organization_id: str
    organization_name: str
    campaigns: List[BisonCampaignResponse]

@router.get(
    "/bison/campaigns",
    response=List[BisonOrganizationCampaignsResponse],
    auth=AuthBearer(),
    summary="Get Bison Campaigns",
    description="Get all campaigns from Bison with connected emails and deliverability scores, grouped by organization"
)
def get_bison_campaigns(request):
    """
    Get all campaigns from Bison with connected emails and deliverability scores, grouped by organization.
    This endpoint now uses cached data from the UserCampaignsBison table for improved performance.
    """
    import time
    start_time = time.time()
    
    user = request.auth
    log_to_terminal("Bison", "Campaigns", f"User {user.email} requested Bison campaigns")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    if not bison_orgs:
        log_to_terminal("Bison", "Campaigns", f"No active Bison organizations found for user {user.email}")
        return []
    
    organizations_campaigns = []
    
    # Import the UserCampaignsBison model
    from analytics.models import UserCampaignsBison
    
    for org in bison_orgs:
        try:
            # Get cached campaigns for this organization
            cached_campaigns = UserCampaignsBison.objects.filter(
                user=user,
                bison_organization=org
            ).order_by('campaign_name')
            
            if not cached_campaigns.exists():
                log_to_terminal("Bison", "Campaigns", f"No cached campaigns found for organization {org.bison_organization_name}")
                # Skip this organization if no campaigns are found
                continue
            
            # Format the campaigns for the response
            org_campaigns = []
            for campaign in cached_campaigns:
                org_campaigns.append({
                    "id": campaign.campaign_id,
                    "name": campaign.campaign_name,
                    "connectedEmails": campaign.connected_emails_count,
                    "sendsPerAccount": campaign.sends_per_account,
                    "googleScore": campaign.google_score,
                    "outlookScore": campaign.outlook_score,
                    "maxDailySends": campaign.max_daily_sends
                })
            
            # Add organization with its campaigns to the response
            if org_campaigns:
                organizations_campaigns.append({
                    "organization_id": str(org.id),
                    "organization_name": org.bison_organization_name,
                    "campaigns": org_campaigns
                })
                
            log_to_terminal("Bison", "Campaigns", f"Retrieved {len(org_campaigns)} cached campaigns for {org.bison_organization_name}")
                
        except Exception as e:
            log_to_terminal("Bison", "Campaigns", f"Error retrieving cached campaigns for organization {org.bison_organization_name}: {str(e)}")
            continue
    
    total_time = time.time() - start_time
    log_to_terminal("Bison", "Campaigns", f"Returning campaigns for {len(organizations_campaigns)} organizations (took {total_time:.2f}s)")
    return organizations_campaigns

# Analytics schemas
class InstantlyAccountsResponse(Schema):
    # existing code
    pass 