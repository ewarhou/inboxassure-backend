from datetime import datetime, timedelta
from typing import List
from ninja import Router
from ninja.security import HttpBearer
from django.db.models import Avg
from django.utils import timezone
from .models import InboxassureReports, ProviderPerformance, ClientOrganizations, InboxassureOrganizations
from pydantic import BaseModel
import uuid

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

def get_client_organizations(client_id: str):
    """Helper function to get all organizations for a client"""
    try:
        return ClientOrganizations.objects.filter(
            client_id=uuid.UUID(client_id)
        ).select_related('organization')
    except ValueError:
        # If client_id is not a UUID, try using it as is
        return ClientOrganizations.objects.filter(
            client_id=client_id
        ).select_related('organization')

class AuthBearer(HttpBearer):
    def authenticate(self, request, token):
        try:
            import jwt
            from django.conf import settings
            from django.contrib.auth import get_user_model
            User = get_user_model()
            
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=['HS256'])
            user = User.objects.get(id=payload['user_id'])
            return {'client_id': str(user.id)}
        except:
            return None

@router.get("/get-sending-power", response=List[SendingPowerResponse], auth=AuthBearer())
def get_sending_power(request):
    """Get sending power over time for all client organizations"""
    client_orgs = get_client_organizations(request.auth['client_id'])
    result = []
    
    for client_org in client_orgs:
        try:
            reports = InboxassureReports.objects.filter(
                client_id=uuid.UUID(request.auth['client_id']),
                organization_id=client_org.organization_id
            ).order_by('report_datetime')
        except ValueError:
            reports = InboxassureReports.objects.filter(
                client_id=request.auth['client_id'],
                organization_id=client_org.organization_id
            ).order_by('report_datetime')
        
        for report in reports:
            result.append(
                SendingPowerResponse(
                    organization_id=str(client_org.organization_id),
                    organization_name=client_org.organization.name,
                    datetime=report.report_datetime,
                    sending_power=report.sending_power
                )
            )
    
    return result

@router.get("/get-account-performance", response=List[AccountPerformanceResponse], auth=AuthBearer())
def get_account_performance(request):
    """Get daily account performance metrics for all client organizations"""
    client_orgs = get_client_organizations(request.auth['client_id'])
    result = []
    
    for client_org in client_orgs:
        try:
            reports = InboxassureReports.objects.filter(
                client_id=uuid.UUID(request.auth['client_id']),
                organization_id=client_org.organization_id
            ).order_by('report_datetime')
        except ValueError:
            reports = InboxassureReports.objects.filter(
                client_id=request.auth['client_id'],
                organization_id=client_org.organization_id
            ).order_by('report_datetime')
        
        for report in reports:
            result.append(
                AccountPerformanceResponse(
                    organization_id=str(client_org.organization_id),
                    organization_name=client_org.organization.name,
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
    two_weeks_ago = timezone.now() - timedelta(days=14)
    client_orgs = get_client_organizations(request.auth['client_id'])
    result = []
    
    for client_org in client_orgs:
        try:
            # Get the latest report for each organization
            latest_report = InboxassureReports.objects.filter(
                client_id=uuid.UUID(request.auth['client_id']),
                organization_id=client_org.organization_id
            ).latest('report_datetime')
        except ValueError:
            latest_report = InboxassureReports.objects.filter(
                client_id=request.auth['client_id'],
                organization_id=client_org.organization_id
            ).latest('report_datetime')
        
        if not latest_report:
            continue
        
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
        
        for provider in providers:
            result.append(
                ProviderPerformanceResponse(
                    organization_id=str(client_org.organization_id),
                    organization_name=client_org.organization.name,
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