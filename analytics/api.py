from datetime import datetime, timedelta
from typing import List
from ninja import Router, Schema
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from .models import InboxassureReports, ProviderPerformance, ClientOrganizations, InboxassureOrganizations
import uuid
from django.db import connections
import logging
from django.db import connection
from django.db.models.sql import Query
from django.db.models import Q
from spamcheck.models import UserSpamcheckReport
from authentication.authorization import AuthBearer

logger = logging.getLogger(__name__)

router = Router(tags=["Analytics"])

class OrganizationData(Schema):
    """Organization data container"""
    organization_id: str
    organization_name: str
    data: dict

    class Config:
        schema_extra = {
            "example": {
                "organization_id": "123e4567-e89b-12d3-a456-426614174000",
                "organization_name": "Example Org",
                "data": {}
            }
        }

class SendingPowerResponse(Schema):
    """Response model for sending power metrics"""
    organization_id: str
    organization_name: str
    datetime: datetime
    sending_power: int

    class Config:
        schema_extra = {
            "description": "Sending power metrics response",
            "fields": {
                "organization_id": "Organization's unique identifier",
                "organization_name": "Name of the organization",
                "datetime": "Timestamp of the measurement",
                "sending_power": "Sending power value"
            }
        }

class AccountPerformanceResponse(Schema):
    """Response model for account performance metrics"""
    organization_id: str
    organization_name: str
    date: datetime
    google_good: int
    google_bad: int
    outlook_good: int
    outlook_bad: int

    class Config:
        schema_extra = {
            "description": "Account performance metrics",
            "fields": {
                "organization_id": "Organization's unique identifier",
                "organization_name": "Name of the organization",
                "date": "Date of the performance measurement",
                "google_good": "Number of good Google deliveries",
                "google_bad": "Number of bad Google deliveries",
                "outlook_good": "Number of good Outlook deliveries",
                "outlook_bad": "Number of bad Outlook deliveries"
            }
        }

class ProviderPerformanceResponse(Schema):
    """Response model for provider performance metrics"""
    organization_id: str
    organization_name: str
    provider: str
    reply_rate: float
    bounce_rate: float
    google_score: float
    outlook_score: float
    overall_score: float

    class Config:
        schema_extra = {
            "description": "Provider performance metrics",
            "fields": {
                "organization_id": "Organization's unique identifier",
                "organization_name": "Name of the organization",
                "provider": "Name of the email provider",
                "reply_rate": "Email reply rate percentage",
                "bounce_rate": "Email bounce rate percentage",
                "google_score": "Google delivery score",
                "outlook_score": "Outlook delivery score",
                "overall_score": "Combined delivery score"
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

def get_client_uuid(auth_id: str):
    """Helper function to get client UUID from auth ID"""
    with connections['default'].cursor() as cursor:
        query = """
            SELECT ic.id, ic.client_email, au.email, au.id as auth_id
            FROM inboxassure_clients ic
            JOIN auth_user au ON au.email = ic.client_email
            WHERE au.id = %s
        """
        print(f"\n[1] STEP 1 - Getting client UUID")
        print(f"Query: {query}")
        print(f"Auth ID: {auth_id}")
        cursor.execute(query, [auth_id])
        result = cursor.fetchone()
        print(f"Result from database: {result}")
        if result:
            client_uuid = result[0]
            print(f"✅ Found client UUID: {client_uuid}")
            return client_uuid
        print(f"❌ No client found for auth_id: {auth_id}")
        return None

def get_client_organizations(client_id):
    """Helper function to get all organizations for a client"""
    print(f"\n[2] STEP 2 - Getting organizations")
    print(f"Input client_id: {client_id}")
    
    # Always convert to string with proper UUID format
    if isinstance(client_id, uuid.UUID):
        client_uuid = str(client_id)
    else:
        try:
            # Try to parse and format as UUID to ensure proper format
            client_uuid = str(uuid.UUID(client_id))
        except ValueError:
            client_uuid = client_id
    
    print(f"Converted client_uuid: {client_uuid}")
    
    # Use raw SQL to ensure proper UUID format
    with connection.cursor() as cursor:
        query = """
            SELECT 
                co.id,
                co.organization_id,
                io.name as organization_name
            FROM client_organizations co
            JOIN inboxassure_organizations io ON co.organization_id = io.id
            WHERE co.client_id = %s
            ORDER BY co.created_at DESC
        """
        print(f"Executing query: {query} with UUID: {client_uuid}")
        cursor.execute(query, [client_uuid])
        rows = cursor.fetchall()
        
        print(f"Organizations found: {len(rows)}")
        
        if rows:
            print("Organization details:")
            orgs = []
            for row in rows:
                org = ClientOrganizations()
                org.id = row[0]
                org.organization = InboxassureOrganizations()
                org.organization.id = row[1]
                org.organization.name = row[2]
                orgs.append(org)
                print(f"  - ID: {org.organization.id}, Name: {org.organization.name}")
        else:
            print("❌ No organizations found")
            orgs = []
        
        return orgs

class AuthBearer(HttpBearer):
    def authenticate(self, request, token):
        try:
            import jwt
            from django.conf import settings
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            logger.info(f"Authenticated user: {user.id} ({user.email})")
            return {'client_id': str(user.id)}
        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            return None

@router.get(
    "/get-sending-power", 
    response=List[SendingPowerResponse], 
    auth=AuthBearer(),
    summary="Sending Power History",
    description="Get sending power metrics over time for all client organizations"
)
def get_sending_power(request):
    """Get sending power over time for all client organizations"""
    print("\n=== Starting get-sending-power endpoint ===")
    auth_id = request.auth['client_id']
    print(f"Auth ID from request: {auth_id}")
    
    client_uuid = get_client_uuid(auth_id)
    print(f"\n[3] STEP 3 - Client UUID result: {client_uuid}")
    
    if not client_uuid:
        print("❌ No client UUID found - returning empty array")
        return []
    
    # Convert string UUID to UUID object if needed
    if isinstance(client_uuid, str):
        client_uuid_obj = uuid.UUID(client_uuid)
    else:
        client_uuid_obj = client_uuid
    
    print(f"Converted UUID object: {client_uuid_obj}")
    
    # Get organizations for this client
    organizations = get_client_organizations(client_uuid_obj)
    org_list = list(organizations)
    print(f"\n[4] STEP 4 - Found {len(org_list)} organizations")
    
    if not org_list:
        print("❌ No organizations found - returning empty array")
        return []
        
    result = []
    for org in org_list:
        print(f"\n[5] STEP 5 - Processing organization: {org.organization.id} - {org.organization.name}")
        
        # Get all reports for this organization using raw SQL
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    id,
                    organization_id,
                    sending_power,
                    report_datetime
                FROM inboxassure_reports
                WHERE client_id = %s
                AND organization_id = %s
                ORDER BY report_datetime
            """
            print(f"Executing reports query with client_id: {client_uuid_obj}, org_id: {org.organization.id}")
            cursor.execute(query, [str(client_uuid_obj), str(org.organization.id)])
            report_rows = cursor.fetchall()
            
            print(f"Reports found: {len(report_rows)}")
            
            if not report_rows:
                print(f"❌ No reports found for organization {org.organization.id}")
                continue
                
            print("Report details:")
            for row in report_rows:
                report_id, org_id, sending_power, report_datetime = row
                print(f"  - ID: {report_id}")
                print(f"    DateTime: {report_datetime}")
                print(f"    Sending Power: {sending_power}")
                result.append(
                    SendingPowerResponse(
                        organization_id=str(org_id),
                        organization_name=org.organization.name,
                        datetime=report_datetime,
                        sending_power=sending_power
                    )
                )
    
    print(f"\n[6] STEP 6 - Final result count: {len(result)}")
    if result:
        print("Result preview:")
        for item in result[:2]:  # Show first 2 items only
            print(f"  - Org: {item.organization_name}")
            print(f"    DateTime: {item.datetime}")
            print(f"    Sending Power: {item.sending_power}")
    else:
        print("❌ No results to return")
    
    return result

@router.get(
    "/get-account-performance", 
    response=List[AccountPerformanceResponse], 
    auth=AuthBearer(),
    summary="Account Performance",
    description="Get daily account performance metrics including Google and Outlook performance for all client organizations"
)
def get_account_performance(request):
    """Get daily account performance metrics for all client organizations"""
    print("\n=== Starting get-account-performance endpoint ===")
    auth_id = request.auth['client_id']
    print(f"Auth ID from request: {auth_id}")
    
    client_uuid = get_client_uuid(auth_id)
    print(f"\n[3] STEP 3 - Client UUID result: {client_uuid}")
    
    if not client_uuid:
        print("❌ No client UUID found - returning empty array")
        return []
    
    # Convert string UUID to UUID object if needed
    if isinstance(client_uuid, str):
        client_uuid_obj = uuid.UUID(client_uuid)
    else:
        client_uuid_obj = client_uuid
    
    print(f"Converted UUID object: {client_uuid_obj}")
    
    # Get organizations for this client
    organizations = get_client_organizations(client_uuid_obj)
    org_list = list(organizations)
    print(f"\n[4] STEP 4 - Found {len(org_list)} organizations")
    
    if not org_list:
        print("❌ No organizations found - returning empty array")
        return []
        
    result = []
    for org in org_list:
        print(f"\n[5] STEP 5 - Processing organization: {org.organization.id} - {org.organization.name}")
        
        # Get all reports for this organization using raw SQL
        with connection.cursor() as cursor:
            query = """
                SELECT 
                    id,
                    organization_id,
                    google_good,
                    google_bad,
                    outlook_good,
                    outlook_bad,
                    report_datetime
                FROM inboxassure_reports
                WHERE client_id = %s
                AND organization_id = %s
                ORDER BY report_datetime
            """
            print(f"Executing reports query with client_id: {client_uuid_obj}, org_id: {org.organization.id}")
            cursor.execute(query, [str(client_uuid_obj), str(org.organization.id)])
            report_rows = cursor.fetchall()
            
            print(f"Reports found: {len(report_rows)}")
            
            if not report_rows:
                print(f"❌ No reports found for organization {org.organization.id}")
                continue
                
            print("Report details:")
            for row in report_rows:
                report_id, org_id, google_good, google_bad, outlook_good, outlook_bad, report_datetime = row
                print(f"  - ID: {report_id}")
                print(f"    DateTime: {report_datetime}")
                print(f"    Google: {google_good}/{google_bad}, Outlook: {outlook_good}/{outlook_bad}")
                result.append(
                    AccountPerformanceResponse(
                        organization_id=str(org_id),
                        organization_name=org.organization.name,
                        date=report_datetime,
                        google_good=google_good,
                        google_bad=google_bad,
                        outlook_good=outlook_good,
                        outlook_bad=outlook_bad
                    )
                )
    
    print(f"\n[6] STEP 6 - Final result count: {len(result)}")
    if result:
        print("Result preview:")
        for item in result[:2]:  # Show first 2 items only
            print(f"  - Org: {item.organization_name}")
            print(f"    DateTime: {item.date}")
            print(f"    Google: {item.google_good}/{item.google_bad}")
            print(f"    Outlook: {item.outlook_good}/{item.outlook_bad}")
    else:
        print("❌ No results to return")
    
    return result

@router.get(
    "/get-provider-performance", 
    response=List[ProviderPerformanceResponse], 
    auth=AuthBearer(),
    summary="Provider Performance",
    description="Get provider performance metrics over the last 14 days including reply rates, bounce rates, and provider scores"
)
def get_provider_performance(request):
    """Get provider performance metrics over last 14 days for all client organizations"""
    print("\n=== Starting get-provider-performance endpoint ===")
    auth_id = request.auth['client_id']
    print(f"Auth ID from request: {auth_id}")
    
    two_weeks_ago = timezone.now() - timedelta(days=14)
    print(f"Getting data since: {two_weeks_ago}")
    
    client_uuid = get_client_uuid(auth_id)
    print(f"\n[3] STEP 3 - Client UUID result: {client_uuid}")
    
    if not client_uuid:
        print("❌ No client UUID found - returning empty array")
        return []
    
    # Convert string UUID to UUID object if needed
    if isinstance(client_uuid, str):
        client_uuid_obj = uuid.UUID(client_uuid)
    else:
        client_uuid_obj = client_uuid
    
    print(f"Converted UUID object: {client_uuid_obj}")
    
    # Get organizations for this client
    organizations = get_client_organizations(client_uuid_obj)
    org_list = list(organizations)
    print(f"\n[4] STEP 4 - Found {len(org_list)} organizations")
    
    if not org_list:
        print("❌ No organizations found - returning empty array")
        return []
        
    result = []
    for org in org_list:
        print(f"\n[5] STEP 5 - Processing organization: {org.organization.id} - {org.organization.name}")
        
        # First get the latest report for the organization
        with connection.cursor() as cursor:
            latest_report_query = """
                SELECT id
                FROM inboxassure_reports
                WHERE client_id = %s
                AND organization_id = %s
                ORDER BY report_datetime DESC
                LIMIT 1
            """
            print(f"Getting latest report for org: {org.organization.id}")
            cursor.execute(latest_report_query, [str(client_uuid_obj), str(org.organization.id)])
            latest_report = cursor.fetchone()
            
            if not latest_report:
                print(f"❌ No reports found for organization {org.organization.id}")
                continue
                
            latest_report_id = latest_report[0]
            print(f"Latest report ID: {latest_report_id}")
            
            # Get provider performance data
            provider_query = """
                SELECT 
                    provider,
                    AVG(reply_rate) as avg_reply_rate,
                    AVG(bounce_rate) as avg_bounce_rate,
                    AVG(google_good_percent) as avg_google_good,
                    AVG(outlook_good_percent) as avg_outlook_good
                FROM provider_performance
                WHERE report_id = %s
                AND created_at >= %s
                GROUP BY provider
            """
            print(f"Getting provider performance data for report: {latest_report_id}")
            cursor.execute(provider_query, [str(latest_report_id), two_weeks_ago])
            provider_rows = cursor.fetchall()
            
            print(f"Providers found: {len(provider_rows)}")
            
            if not provider_rows:
                print(f"❌ No provider data found for report {latest_report_id}")
                continue
                
            print("Provider details:")
            for row in provider_rows:
                provider, reply_rate, bounce_rate, google_score, outlook_score = row
                print(f"  - Provider: {provider}")
                print(f"    Reply Rate: {reply_rate or 0:.2f}")
                print(f"    Bounce Rate: {bounce_rate or 0:.2f}")
                print(f"    Google Score: {google_score or 0:.2f}")
                print(f"    Outlook Score: {outlook_score or 0:.2f}")
                
                result.append(
                    ProviderPerformanceResponse(
                        organization_id=str(org.organization.id),
                        organization_name=org.organization.name,
                        provider=provider,
                        reply_rate=round(float(reply_rate or 0), 2),
                        bounce_rate=round(float(bounce_rate or 0), 2),
                        google_score=round(float(google_score or 0), 2),
                        outlook_score=round(float(outlook_score or 0), 2),
                        overall_score=round(
                            (float(google_score or 0) + float(outlook_score or 0)) / 2, 2
                        )
                    )
                )
    
    print(f"\n[6] STEP 6 - Final result count: {len(result)}")
    if result:
        print("Result preview:")
        for item in result[:2]:  # Show first 2 items only
            print(f"  - Org: {item.organization_name}")
            print(f"    Provider: {item.provider}")
            print(f"    Reply Rate: {item.reply_rate}")
            print(f"    Bounce Rate: {item.bounce_rate}")
            print(f"    Scores - Google: {item.google_score}, Outlook: {item.outlook_score}")
    else:
        print("❌ No results to return")
    
    return result 

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
    
    # Get user from auth
    user_id = request.auth['client_id']
    user = User.objects.get(id=user_id)
    
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
        cursor.execute(query, [user_id])
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