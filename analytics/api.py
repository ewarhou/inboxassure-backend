from datetime import datetime
from typing import List, Optional, Dict, Any
from ninja import Router, Schema, Query
from ninja.pagination import paginate
from ninja.security import HttpBearer
from django.db.models import Avg, Max
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
    status: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None

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
    """Get daily sending power metrics per Bison workspace using cached data
    
    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    from analytics.models import UserBisonSendingPower
    
    user = request.auth
    log_to_terminal("BisonSendingPower", "Request", f"Request for {user.email}")
    
    # Set default date range if not provided (last 30 days)
    if not end_date:
        end_date = timezone.now().date().isoformat()
    if not start_date:
        start_date = (timezone.now() - timezone.timedelta(days=30)).date().isoformat()
    
    log_to_terminal("BisonSendingPower", "DateRange", f"Range: {start_date} to {end_date}")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    if not bison_orgs:
        log_to_terminal("BisonSendingPower", "Warning", "No active Bison organizations found")
        return {"data": []}
    
    data = []
    
    # Convert string dates to date objects for filtering
    start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
    end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
    
    # Get sending power records for all organizations within the date range
    sending_power_records = UserBisonSendingPower.objects.filter(
        user=user,
        bison_organization__in=bison_orgs,
        report_date__gte=start_date_obj,
        report_date__lte=end_date_obj
    ).order_by('-report_date', 'bison_organization__bison_organization_name')
    
    log_to_terminal("BisonSendingPower", "Records", f"Found {sending_power_records.count()} sending power records")
    
    # Format the data for the response
    for record in sending_power_records:
        data.append({
            "date": record.report_date.isoformat(),
            "workspace_name": record.bison_organization.bison_organization_name,
            "sending_power": record.sending_power
        })
    
    log_to_terminal("BisonSendingPower", "Output", f"Returning {len(data)} sending power entries")
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
    google_score: float
    outlook_score: float
    overall_score: float
    sending_power: int
    emails_sent_count: int
    bounced_count: int
    unique_replied_count: int

    class Config:
        schema_extra = {
            "description": "Bison provider performance metrics",
            "example": {
                "organization": "Karpos",
                "provider": "Gmail",
                "start_date": "2024-02-01",
                "end_date": "2024-02-18",
                "total_accounts": 125,
                "google_score": 0.92,
                "outlook_score": 0.88,
                "overall_score": 0.90,
                "sending_power": 3125,
                "emails_sent_count": 15000,
                "bounced_count": 150,
                "unique_replied_count": 750
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
    Get Bison provider performance metrics within a date range using cached data.
    Returns pre-calculated metrics per provider from the UserBisonProviderPerformance table.
    
    Args:
        start_date: Optional start date (YYYY-MM-DD)
        end_date: Optional end date (YYYY-MM-DD)
    """
    from analytics.models import UserBisonProviderPerformance
    
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
    
    data = []
    
    # Process each Bison organization
    for org in bison_orgs:
        log_to_terminal("BisonPerformance", "Processing", f"Organization: {org.bison_organization_name}")
        
        try:
            # Get the most recent provider performance records for this organization
            # Using a different approach that works with MySQL instead of PostgreSQL-specific distinct
            from django.db.models import Max
            
            # First, get the latest created_at for each provider
            latest_records = UserBisonProviderPerformance.objects.filter(
                user=user,
                bison_organization=org
            ).values('provider_name').annotate(
                latest_created=Max('created_at')
            )
            
            log_to_terminal("BisonPerformance", "Debug", f"Found {len(latest_records)} unique providers")
            
            # Then, get the actual records using the provider name and latest created_at
            performance_records = []
            for record in latest_records:
                provider_record = UserBisonProviderPerformance.objects.filter(
                    user=user,
                    bison_organization=org,
                    provider_name=record['provider_name'],
                    created_at=record['latest_created']
                ).first()
                
                if provider_record:
                    performance_records.append(provider_record)
            
            if not performance_records:
                log_to_terminal("BisonPerformance", "Warning", f"No provider performance records found for {org.bison_organization_name}")
                continue
            
            log_to_terminal("BisonPerformance", "Debug", f"Found {len(performance_records)} provider performance records")
            
            # Add each provider's performance data to the response
            for record in performance_records:
                data.append({
                    "organization": org.bison_organization_name,
                    "provider": record.provider_name,
                    "start_date": record.start_date.isoformat(),
                    "end_date": record.end_date.isoformat(),
                    "total_accounts": record.total_accounts,
                    "google_score": record.google_score,
                    "outlook_score": record.outlook_score,
                    "overall_score": record.overall_score,
                    "sending_power": record.sending_power,
                    "emails_sent_count": record.emails_sent_count,
                    "bounced_count": record.bounced_count,
                    "unique_replied_count": record.unique_replied_count
                })
                
        except Exception as e:
            log_to_terminal("BisonPerformance", "Error", f"Error processing org {org.bison_organization_name}: {str(e)}")
            import traceback
            log_to_terminal("BisonPerformance", "Error", f"Traceback: {traceback.format_exc()}")
            continue
    
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
    """Get dashboard summary metrics per Bison organization using cached data"""
    from django.contrib.auth import get_user_model
    from settings.models import UserBison
    from analytics.models import UserBisonDashboardSummary
    
    User = get_user_model()
    user = request.auth
    
    log_to_terminal("BisonDashboard", "API", f"User {user.email} requested dashboard summary")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    log_to_terminal("BisonDashboard", "API", f"Found {bison_orgs.count()} active Bison organizations")
    
    if not bison_orgs:
        log_to_terminal("BisonDashboard", "API", "No active Bison organizations found")
        return []
    
    summaries = []
    
    for org in bison_orgs:
        try:
            log_to_terminal("BisonDashboard", "API", f"Processing org: {org.bison_organization_name}")
            
            # Get the most recent dashboard summary for this organization
            latest_summary = UserBisonDashboardSummary.objects.filter(
                user=user,
                bison_organization=org
            ).order_by('-created_at').first()
            
            if not latest_summary:
                log_to_terminal("BisonDashboard", "API", f"No dashboard summary found for {org.bison_organization_name}")
                # Skip this organization if no summary is found
                continue
            
            log_to_terminal("BisonDashboard", "API", f"Found dashboard summary from {latest_summary.created_at}")
            
            summaries.append({
                "organization_id": str(org.id),
                "organization_name": org.bison_organization_name,
                "checked_accounts": latest_summary.checked_accounts,
                "at_risk_accounts": latest_summary.at_risk_accounts,
                "protected_accounts": latest_summary.protected_accounts,
                "spam_emails_count": latest_summary.spam_emails_count,
                "inbox_emails_count": latest_summary.inbox_emails_count,
                "spam_emails_percentage": latest_summary.spam_emails_percentage,
                "inbox_emails_percentage": latest_summary.inbox_emails_percentage,
                "overall_deliverability": latest_summary.overall_deliverability,
                "last_check_date": latest_summary.created_at.isoformat()
            })
            
            log_to_terminal("BisonDashboard", "API", f"Added summary for {org.bison_organization_name} to response")
                
        except Exception as e:
            log_to_terminal("BisonDashboard", "Error", f"Error processing org {org.bison_organization_name}: {str(e)}")
            import traceback
            log_to_terminal("BisonDashboard", "Error", f"Traceback: {traceback.format_exc()}")
            continue
    
    log_to_terminal("BisonDashboard", "API", f"Returning {len(summaries)} organization summaries")
    return summaries

class BisonOrganizationCampaignsResponse(BaseModel):
    organization_id: str
    organization_name: str
    campaigns: List[BisonCampaignResponse]

class PaginatedBisonCampaignsResponse(BaseModel):
    data: List[BisonOrganizationCampaignsResponse]
    meta: dict

@router.get(
    "/bison/campaigns",
    response=PaginatedBisonCampaignsResponse,
    auth=AuthBearer(),
    summary="Get Bison Campaigns (Cached)",
    description="Get all campaigns from Bison with connected emails and deliverability scores, grouped by organization. Uses locally cached data for performance."
)
def get_bison_campaigns(request, page: int = 1, per_page: int = 10, search: Optional[str] = None, workspace: Optional[int] = None):
    """
    Get all campaigns from Bison with connected emails and deliverability scores, grouped by organization.
    This endpoint now uses cached data from the UserCampaignsBison table for improved performance.
    
    Args:
        page: Page number (default: 1)
        per_page: Number of campaigns per page (default: 10)
        search: Search term to filter campaigns by name (optional)
        workspace: Filter campaigns by workspace ID (optional)
    """
    import time
    start_time = time.time()
    
    user = request.auth
    log_to_terminal("Bison", "Campaigns", f"User {user.email} requested Bison campaigns (page {page}, per_page {per_page}, search: {search or 'None'}, workspace: {workspace or 'None'})")
    
    # Get all active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )
    
    # Filter by workspace if provided
    if workspace:
        bison_orgs = bison_orgs.filter(id=workspace)
        log_to_terminal("Bison", "Campaigns", f"Filtering by workspace ID: {workspace}")
    
    if not bison_orgs:
        log_to_terminal("Bison", "Campaigns", f"No active Bison organizations found for user {user.email}")
        return {"data": [], "meta": {"current_page": page, "per_page": per_page, "total": 0, "total_pages": 0}}
    
    organizations_campaigns = []
    all_campaigns = []
    
    # Import the UserCampaignsBison model
    from analytics.models import UserCampaignsBison
    
    for org in bison_orgs:
        try:
            # Get cached campaigns for this organization
            query = UserCampaignsBison.objects.filter(
                user=user,
                bison_organization=org
            )
            
            # Apply search filter if provided
            if search:
                query = query.filter(campaign_name__icontains=search)
                
            cached_campaigns = query.order_by('campaign_name')
            
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
                all_campaigns.extend(org_campaigns)
                
            log_to_terminal("Bison", "Campaigns", f"Retrieved {len(org_campaigns)} cached campaigns for {org.bison_organization_name}")
                
        except Exception as e:
            log_to_terminal("Bison", "Campaigns", f"Error retrieving cached campaigns for organization {org.bison_organization_name}: {str(e)}")
            continue
    
    # Calculate pagination
    total_campaigns = len(all_campaigns)
    total_pages = (total_campaigns + per_page - 1) // per_page  # Ceiling division
    
    # Apply pagination to organizations_campaigns
    paginated_organizations = []
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    
    # Create a copy of organizations_campaigns with paginated campaigns
    for org_data in organizations_campaigns:
        # Create a new organization with paginated campaigns
        paginated_org = {
            "organization_id": org_data["organization_id"],
            "organization_name": org_data["organization_name"],
            "campaigns": []
        }
        
        # Add campaigns that fall within the current page
        for campaign in org_data["campaigns"]:
            if start_idx <= 0:
                if len(paginated_org["campaigns"]) < per_page:
                    paginated_org["campaigns"].append(campaign)
                else:
                    break
            else:
                start_idx -= 1
        
        # Only add organizations that have campaigns after pagination
        if paginated_org["campaigns"]:
            paginated_organizations.append(paginated_org)
    
    total_time = time.time() - start_time
    log_to_terminal("Bison", "Campaigns", f"Returning campaigns for page {page}/{total_pages} (took {total_time:.2f}s)")
    
    return {
        "data": paginated_organizations,
        "meta": {
            "current_page": page,
            "per_page": per_page,
            "total": total_campaigns,
            "total_pages": total_pages
        }
    }

class BisonDirectCampaignData(BaseModel):
    # Structure mirroring Bison API response for /api/campaigns
    data: List[Dict[str, Any]] # List of raw campaign objects from Bison API
    meta: Dict[str, Any]       # Raw meta object from Bison API (pagination, etc.)

class BisonDirectOrganizationCampaignsResponse(BaseModel):
    organization_id: str
    organization_name: str
    bison_response: Optional[BisonDirectCampaignData] = None # Raw response for this org
    error: Optional[str] = None # To indicate if fetching failed for this org

@router.get(
    "/bison/campaigns-bison",
    response=List[BisonDirectOrganizationCampaignsResponse], # New endpoint returns a list
    auth=AuthBearer(),
    summary="Get Bison Campaigns (Direct)",
    description="Get campaigns directly from the Bison API for each organization, returning the raw paginated response per organization. Fetches all campaign statuses."
)
def get_bison_campaigns_direct(request, page: int = 1, per_page: int = 10, search: Optional[str] = None, workspace: Optional[int] = None):
    """
    Fetches campaigns directly from the Bison API for the user's organizations.

    Args:
        page: Page number to request from Bison API (default: 1)
        per_page: Number of campaigns per page to request from Bison API (default: 10)
        search: Search term to pass to Bison API (optional, uses 'query' parameter)
        workspace: Filter campaigns by a specific workspace ID (optional)
    """
    start_time = time.time()
    user = request.auth
    log_to_terminal("BisonDirect", "Campaigns", f"User {user.email} requested direct Bison campaigns (page {page}, per_page {per_page}, search: {search or 'None'}, workspace: {workspace or 'None'})")

    # Get active Bison organizations for the user
    bison_orgs = UserBison.objects.filter(
        user=user,
        bison_organization_status=True
    )

    # Filter by workspace if provided
    if workspace:
        bison_orgs = bison_orgs.filter(id=workspace)
        log_to_terminal("BisonDirect", "Campaigns", f"Filtering by workspace ID: {workspace}")

    if not bison_orgs.exists():
        log_to_terminal("BisonDirect", "Campaigns", f"No active Bison organizations found for user {user.email} matching criteria.")
        # Return empty list instead of 404, consistent with the cached endpoint
        return []

    results = []
    api_call_count = 0

    for org in bison_orgs:
        org_response = BisonDirectOrganizationCampaignsResponse(
            organization_id=str(org.id),
            organization_name=org.bison_organization_name
        )
        
        api_url = f"{org.base_url.rstrip('/')}/api/campaigns"
        headers = {
            "Authorization": f"Bearer {org.bison_organization_api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        params = {
            "page": page,
            "per_page": per_page,
        }
        # Assuming Bison uses 'query' for search based on common practices
        if search:
            params["query"] = search
            
        log_to_terminal("BisonDirect", "Campaigns", f"Fetching from Bison for org '{org.bison_organization_name}': {api_url} with params: {params}")
        
        try:
            api_start_time = time.time()
            response = requests.get(api_url, headers=headers, params=params, timeout=30) # Added timeout
            api_call_count += 1
            api_duration = time.time() - api_start_time
            log_to_terminal("BisonDirect", "Campaigns", f"Bison API call for org '{org.bison_organization_name}' took {api_duration:.2f}s, Status: {response.status_code}")

            response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
            
            # Attempt to parse JSON, handle potential errors
            try:
                bison_data = response.json()
                # Validate structure slightly - check for 'data' and 'meta' keys
                if isinstance(bison_data, dict) and 'data' in bison_data and 'meta' in bison_data:
                     org_response.bison_response = BisonDirectCampaignData(
                         data=bison_data.get('data', []),
                         meta=bison_data.get('meta', {})
                     )
                else:
                    log_to_terminal("BisonDirect", "Error", f"Unexpected JSON structure from Bison for org '{org.bison_organization_name}'. Response: {bison_data}")
                    org_response.error = "Received unexpected data structure from Bison API."
                    
            except json.JSONDecodeError:
                log_to_terminal("BisonDirect", "Error", f"Failed to decode JSON response from Bison for org '{org.bison_organization_name}'. Status: {response.status_code}, Response text: {response.text[:200]}")
                org_response.error = f"Failed to decode Bison API response (Status: {response.status_code})."
                
        except requests.exceptions.RequestException as e:
            log_to_terminal("BisonDirect", "Error", f"Error fetching campaigns from Bison API for org '{org.bison_organization_name}': {e}")
            org_response.error = f"Failed to connect to Bison API: {e}"
            # Optionally include status code if available in the exception
            if hasattr(e, 'response') and e.response is not None:
                 org_response.error += f" (Status: {e.response.status_code})"

        except Exception as e:
            # Catch any other unexpected errors during processing
            log_to_terminal("BisonDirect", "Error", f"Unexpected error processing org '{org.bison_organization_name}': {e}")
            import traceback
            log_to_terminal("BisonDirect", "Error", f"Traceback: {traceback.format_exc()}")
            org_response.error = f"An unexpected error occurred: {e}"

        results.append(org_response)

    total_time = time.time() - start_time
    log_to_terminal("BisonDirect", "Campaigns", f"Finished request for user {user.email} in {total_time:.2f}s. Made {api_call_count} Bison API calls.")
    
    # Return the list of organization responses
    return results

# Analytics schemas
class InstantlyAccountsResponse(Schema):
    # existing code
    pass 