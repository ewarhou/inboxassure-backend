from ninja import Schema
from typing import Optional, List
from datetime import datetime

class InstantlyEditorAccountSchema(Schema):
    instantly_editor_email: str
    instantly_editor_password: str

class InstantlyApiKeySchema(Schema):
    instantly_api_key: str
    instantly_user_token: str

class EmailGuardApiKeySchema(Schema):
    emailguard_api_key: str

class BisonOrganizationSchema(Schema):
    bison_organization_name: str
    bison_organization_api_key: str

class StatusResponseSchema(Schema):
    status: bool
    message: str

class ErrorResponseSchema(Schema):
    detail: str

class SuccessResponseSchema(Schema):
    message: str
    data: dict

class BisonOrganizationResponseSchema(Schema):
    id: int
    bison_organization_name: str
    bison_organization_api_key: str
    bison_organization_status: Optional[bool]
    created_at: datetime
    updated_at: datetime

class InstantlyOrganizationResponseSchema(Schema):
    id: int
    instantly_organization_id: str
    instantly_organization_name: str
    instantly_organization_token: str
    instantly_organization_status: Optional[bool]
    created_at: datetime
    updated_at: datetime

class CheckEmailGuardStatusSchema(Schema):
    emailguard_api_key: str

class CheckInstantlyStatusSchema(Schema):
    email: str
    password: str

class InstantlyOrganizationAuthSchema(Schema):
    orgID: str

class InstantlyOrganizationDataSchema(Schema):
    id: str
    name: str
    owner: str
    org_logo_url: Optional[str]
    org_client_domain: Optional[str] 