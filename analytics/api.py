from datetime import datetime
from typing import List, Optional
from ninja import Router, Schema
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from django.db import connection
from authentication.authorization import AuthBearer

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