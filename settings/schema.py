from ninja import Schema
from typing import Optional, List
from datetime import datetime

class InstantlyEditorAccountSchema(Schema):
    instantly_editor_email: str
    instantly_editor_password: str

class InstantlyEditorAccountResponseSchema(Schema):
    instantly_editor_email: Optional[str]
    instantly_editor_password: Optional[str]
    instantly_status: Optional[bool]

class EmailGuardKeyResponseSchema(Schema):
    emailguard_api_key: Optional[str]
    emailguard_status: Optional[bool]

class BisonKeyResponseSchema(Schema):
    bison_organization_name: str
    bison_organization_api_key: str
    bison_organization_status: Optional[bool]

class InstantlyApiKeySchema(Schema):
    instantly_api_key: str
    organization_id: int

class EmailGuardApiKeySchema(Schema):
    emailguard_api_key: str

class BisonOrganizationSchema(Schema):
    bison_organization_name: str
    bison_organization_api_key: str
    base_url: str = 'https://app.orbitmailboost.com'

class InstantlyOrganizationInfo(Schema):
    id: int  # Our database ID
    uuid: str  # Instantly's organization ID
    name: str

class InstantlyStatusResponseSchema(Schema):
    status: bool
    message: str
    user_id: Optional[str]
    organizations: List[InstantlyOrganizationInfo]

class InstantlyApiKeyCheckResponseSchema(Schema):
    status: bool
    message: str

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

class UpdateTimezoneSchema(Schema):
    timezone: str

class UpdateProfileSchema(Schema):
    timezone: Optional[str] = None

class BisonWorkspaceRequestSchema(Schema):
    base_url: str
    admin_api_key: str

class BisonWorkspaceResponseSchema(Schema):
    id: int
    name: str
    organization_id: int
    api_key: str
    status: bool

class BisonWorkspacesResponseSchema(Schema):
    workspaces: List[BisonWorkspaceResponseSchema]
    message: str

class BisonTagsResponseSchema(Schema):
    tags: List[str]
    message: str

class TestWebhookSchema(Schema):
    webhook_url: str 