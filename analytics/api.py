from datetime import datetime, timedelta
from typing import List
from ninja import Router
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from .models import InboxassureReports, ProviderPerformance, ClientOrganizations, InboxassureOrganizations
from pydantic import BaseModel
import uuid
from django.db import connections
import logging

logger = logging.getLogger(__name__)

router = Router()

class OrganizationData(BaseModel):
    organization_id: str
    organization_name: str
    data: dict

class SendingPowerResponse(BaseModel):
    organization_id: str
    organization_name: str
    datetime: datetime
    sending_power: int

class AccountPerformanceResponse(BaseModel):
    organization_id: str
    organization_name: str
    date: datetime
    google_good: int
    google_bad: int
    outlook_good: int
    outlook_bad: int

class ProviderPerformanceResponse(BaseModel):
    organization_id: str
    organization_name: str
    provider: str
    reply_rate: float
    bounce_rate: float
    google_score: float
    outlook_score: float
    overall_score: float

def get_client_uuid(auth_id: str):
    """Helper function to get client UUID from auth ID"""
    with connections['default'].cursor() as cursor:
        query = """
            SELECT ic.id, ic.client_email, au.email, au.id as auth_id
            FROM inboxassure_clients ic
            JOIN auth_user au ON au.email = ic.client_email
            WHERE au.id = %s
        """
        logger.info(f"Executing query: {query} with auth_id: {auth_id}")
        cursor.execute(query, [auth_id])
        result = cursor.fetchone()
        logger.info(f"Query result: {result}")
        if result:
            client_uuid = result[0]
            logger.info(f"Found client UUID: {client_uuid} for auth_id: {auth_id}")
            return client_uuid
        logger.warning(f"No client found for auth_id: {auth_id}")
        return None

def get_client_organizations(client_id: str):
    """Helper function to get all organizations for a client"""
    client_uuid = get_client_uuid(client_id)
    logger.info(f"Got client UUID: {client_uuid} for client_id: {client_id}")
    if not client_uuid:
        return []
    orgs = ClientOrganizations.objects.filter(
        client_id=client_uuid
    ).select_related('organization')
    logger.info(f"Found {orgs.count()} organizations for client")
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

@router.get("/get-sending-power", response=List[SendingPowerResponse], auth=AuthBearer())
def get_sending_power(request):
    """Get sending power over time for all client organizations"""
    logger.info(f"Getting sending power for client_id: {request.auth['client_id']}")
    client_uuid = get_client_uuid(request.auth['client_id'])
    logger.info(f"Retrieved client_uuid: {client_uuid}")
    
    if not client_uuid:
        logger.warning(f"No client UUID found for auth_id: {request.auth['client_id']}")
        return []
    
    # Convert string UUID to UUID object
    client_uuid_obj = uuid.UUID(client_uuid)
    
    # Get all reports for this client
    reports = InboxassureReports.objects.filter(
        client_id=client_uuid_obj,
        organization_id=uuid.UUID("d9749aa5-84a6-4e56-9ff1-08d6f9de1d20")  # hardcoded for testing
    ).order_by('report_datetime')
    
    report_count = reports.count()
    logger.info(f"Found {report_count} reports for client {client_uuid}")
    
    if report_count == 0:
        logger.warning(f"No reports found for client {client_uuid}")
        return []
    
    result = []
    for report in reports:
        logger.info(f"Adding report {report.id} with sending_power: {report.sending_power}")
        result.append(
            SendingPowerResponse(
                organization_id=str(report.organization_id),
                organization_name="sales society",  # hardcoded for testing
                datetime=report.report_datetime,
                sending_power=report.sending_power
            )
        )
    
    logger.info(f"Returning {len(result)} total reports")
    return result

@router.get("/get-account-performance", response=List[AccountPerformanceResponse], auth=AuthBearer())
def get_account_performance(request):
    """Get daily account performance metrics for all client organizations"""
    logger.info(f"Getting account performance for client_id: {request.auth['client_id']}")
    client_uuid = get_client_uuid(request.auth['client_id'])
    if not client_uuid:
        logger.warning(f"No client UUID found for auth_id: {request.auth['client_id']}")
        return []
    
    # Convert string UUID to UUID object
    client_uuid_obj = uuid.UUID(client_uuid)
    
    # Get all reports for this client
    reports = InboxassureReports.objects.filter(
        client_id=client_uuid_obj,
        organization_id=uuid.UUID("d9749aa5-84a6-4e56-9ff1-08d6f9de1d20")  # hardcoded for testing
    ).order_by('report_datetime')
    
    report_count = reports.count()
    logger.info(f"Found {report_count} reports for client {client_uuid}")
    
    if report_count == 0:
        logger.warning(f"No reports found for client {client_uuid}")
        return []
    
    result = []
    for report in reports:
        result.append(
            AccountPerformanceResponse(
                organization_id=str(report.organization_id),
                organization_name="sales society",  # hardcoded for testing
                date=report.report_datetime,
                google_good=report.google_good,
                google_bad=report.google_bad,
                outlook_good=report.outlook_good,
                outlook_bad=report.outlook_bad
            )
        )
    
    return result

@router.get("/get-provider-performance", response=List[ProviderPerformanceResponse], auth=AuthBearer())
def get_provider_performance(request):
    """Get provider performance metrics over last 14 days for all client organizations"""
    logger.info(f"Getting provider performance for client_id: {request.auth['client_id']}")
    two_weeks_ago = timezone.now() - timedelta(days=14)
    
    client_uuid = get_client_uuid(request.auth['client_id'])
    if not client_uuid:
        logger.warning(f"No client UUID found for auth_id: {request.auth['client_id']}")
        return []
    
    # Convert string UUID to UUID object
    client_uuid_obj = uuid.UUID(client_uuid)
    
    # Get the latest report for the organization
    try:
        latest_report = InboxassureReports.objects.filter(
            client_id=client_uuid_obj,
            organization_id=uuid.UUID("d9749aa5-84a6-4e56-9ff1-08d6f9de1d20")  # hardcoded for testing
        ).latest('report_datetime')
        logger.info(f"Found latest report for organization")
    except InboxassureReports.DoesNotExist:
        logger.warning(f"No reports found for organization")
        return []
    
    # Get provider performance data
    providers = ProviderPerformance.objects.filter(
        report_id=latest_report.id,
        created_at__gte=two_weeks_ago
    ).values('provider').annotate(
        avg_reply_rate=Avg('reply_rate'),
        avg_bounce_rate=Avg('bounce_rate'),
        avg_google_good=Avg('google_good_percent'),
        avg_outlook_good=Avg('outlook_good_percent')
    )
    logger.info(f"Found {len(providers)} providers")
    
    result = []
    for provider in providers:
        result.append(
            ProviderPerformanceResponse(
                organization_id=str(latest_report.organization_id),
                organization_name="sales society",  # hardcoded for testing
                provider=provider['provider'],
                reply_rate=round(provider['avg_reply_rate'] or 0, 2),
                bounce_rate=round(provider['avg_bounce_rate'] or 0, 2),
                google_score=round(provider['avg_google_good'] or 0, 2),
                outlook_score=round(provider['avg_outlook_good'] or 0, 2),
                overall_score=round(
                    ((provider['avg_google_good'] or 0) + 
                     (provider['avg_outlook_good'] or 0)) / 2, 2
                )
            )
        )
    
    return result 