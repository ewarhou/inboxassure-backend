from typing import List, Optional, Dict, Any
from datetime import datetime
from ninja import Schema, Field
from uuid import UUID
from ninja.errors import HttpError

class SpamcheckAccountSchema(Schema):
    email_account: str = Field(..., description="Email account to use for spamcheck")

class CreateSpamcheckSchema(Schema):
    name: str = Field(..., description="Name of the spamcheck")
    user_organization_id: int = Field(..., description="ID of the Instantly organization to use")
    accounts: List[str] = Field(..., description="List of email accounts to check")
    open_tracking: bool = Field(..., description="Whether to track email opens")
    link_tracking: bool = Field(..., description="Whether to track link clicks")
    text_only: bool = Field(..., description="Whether to send text-only emails")
    subject: str = Field(..., description="Email subject template")
    body: str = Field(..., description="Email body template")
    scheduled_at: datetime = Field(..., description="When to run the spamcheck")
    recurring_days: Optional[int] = Field(None, description="Number of days for recurring checks")
    is_domain_based: bool = Field(default=False, description="Whether to filter accounts by domain and use one per domain")
    conditions: Optional[str] = Field(None, description="Conditions for sending limit (e.g., 'google>=0.5andoutlook>=0.5sending=10/0')")
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports (0 for immediate, 0.5 for 30min, 1 for 1h, etc). Default is 1h")

    def validate_accounts(self, accounts: List[str]) -> List[str]:
        if not accounts:
            raise HttpError(400, "At least one email account is required")
        return accounts

class UpdateSpamcheckSchema(Schema):
    name: Optional[str] = Field(None, description="Name of the spamcheck")
    accounts: Optional[List[str]] = Field(None, description="List of email accounts to check")
    open_tracking: Optional[bool] = Field(None, description="Whether to track email opens")
    link_tracking: Optional[bool] = Field(None, description="Whether to track link clicks")
    text_only: Optional[bool] = Field(None, description="Whether to send text-only emails")
    subject: Optional[str] = Field(None, description="Email subject template")
    body: Optional[str] = Field(None, description="Email body template")
    scheduled_at: Optional[datetime] = Field(None, description="When to run the spamcheck")
    recurring_days: Optional[int] = Field(None, description="Number of days for recurring checks")
    conditions: Optional[str] = Field(None, description="Conditions for sending limit (e.g., 'google>=0.5andoutlook>=0.5sending=10/0')")
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports (0 for immediate, 0.5 for 30min, 1 for 1h, etc). Default is 1h")

class LaunchSpamcheckSchema(Schema):
    spamcheck_id: int = Field(..., description="ID of the spamcheck to launch")
    is_test: bool = Field(default=False, description="Whether this is a test launch")

class AccountPayloadNameSchema(Schema):
    last: Optional[str]
    first: Optional[str]

class AccountPayloadWarmupAdvancedSchema(Schema):
    warm_ctd: Optional[bool] = None
    open_rate: Optional[float] = None
    random_range: Optional[Dict[str, Any]] = None
    weekday_only: Optional[bool] = None
    important_rate: Optional[float] = None
    read_emulation: Optional[bool] = None
    spam_save_rate: Optional[float] = None

class AccountPayloadWarmupSchema(Schema):
    limit: Optional[str] = None
    advanced: Optional[AccountPayloadWarmupAdvancedSchema] = None
    increment: Optional[str] = None
    reply_rate: Optional[str] = None

class AccountPayloadSchema(Schema):
    name: Optional[AccountPayloadNameSchema] = None
    warmup: Optional[AccountPayloadWarmupSchema] = None
    provider: Optional[str] = None
    daily_limit: Optional[str] = None
    sending_gap: Optional[str] = None
    enable_slow_ramp: Optional[bool] = None

class AccountTagSchema(Schema):
    id: Optional[str] = None
    label: Optional[str] = None
    description: Optional[str] = None
    resource_id: Optional[str] = None

class AccountSchema(Schema):
    email: Optional[str] = None
    timestamp_created: Optional[str] = None
    timestamp_updated: Optional[str] = None
    payload: Optional[AccountPayloadSchema] = None
    organization: Optional[str] = None
    status: Optional[int] = None
    warmup_status: Optional[int] = None
    provider_code: Optional[int] = None
    stat_warmup_score: Optional[float] = None
    tags: Optional[List[AccountTagSchema]] = []

class ListAccountsDataSchema(Schema):
    organization_id: int
    organization_name: str
    total_accounts: int
    accounts: List[str]

class ListAccountsRequestSchema(Schema):
    platform: str = Field("instantly", description="Platform to fetch accounts from (instantly or bison)")
    search: Optional[str] = Field(None, description="Filter accounts by content")
    ignore_tag: Optional[str] = Field(None, description="Don't include accounts with this tag title")
    is_active: Optional[bool] = Field(None, description="Include ONLY active accounts")
    limit: Optional[int] = Field(10, description="Number of accounts to return")

class ListAccountsResponseSchema(Schema):
    success: bool
    message: str
    data: ListAccountsDataSchema

class SpamcheckDetailsSchema(Schema):
    id: int
    name: str
    status: str
    scheduled_at: datetime
    recurring_days: Optional[int]
    is_domain_based: bool
    conditions: Optional[str]
    reports_waiting_time: Optional[float]
    created_at: datetime
    updated_at: datetime
    user_organization_id: int
    organization_name: str
    accounts_count: int
    campaigns_count: int
    options: dict

class ListSpamchecksResponseSchema(Schema):
    success: bool
    message: str
    data: List[SpamcheckDetailsSchema]

class CreateSpamcheckBisonSchema(Schema):
    name: str
    user_organization_id: int
    accounts: List[str]
    text_only: bool = Field(default=False)
    subject: str
    body: str
    scheduled_at: datetime
    recurring_days: Optional[int] = None
    is_domain_based: bool = Field(default=False)
    conditions: Optional[str] = None
    reports_waiting_time: Optional[float] = Field(default=1.0)

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Test Spamcheck",
                "user_organization_id": 1,
                "accounts": ["test@example.com"],
                "text_only": True,
                "subject": "Test Subject",
                "body": "Test Body",
                "scheduled_at": "2025-02-23T00:17:51.315Z",
                "recurring_days": 0,
                "is_domain_based": False,
                "conditions": "google>=0.5andoutlook>=0.5",
                "reports_waiting_time": 1.0
            }
        }

class UpdateSpamcheckBisonSchema(Schema):
    name: Optional[str] = Field(None, description="Name of the spamcheck")
    accounts: Optional[List[str]] = Field(None, description="List of email accounts to check")
    text_only: Optional[bool] = Field(None, description="Whether to send text-only emails")
    subject: Optional[str] = Field(None, description="Email subject template")
    body: Optional[str] = Field(None, description="Email body template")
    scheduled_at: Optional[datetime] = Field(None, description="When to run the spamcheck")
    recurring_days: Optional[int] = Field(None, description="Number of days for recurring checks")
    is_domain_based: Optional[bool] = Field(None, description="Whether to filter accounts by domain")
    conditions: Optional[str] = Field(None, description="Conditions for sending")
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports")

    class Config:
        json_schema_extra = {
            "example": {
                "name": "Updated Test Spamcheck",
                "accounts": ["test@example.com"],
                "text_only": True,
                "subject": "Updated Subject",
                "body": "Updated Body",
                "scheduled_at": "2025-02-23T00:17:51.315Z",
                "recurring_days": 7,
                "is_domain_based": False,
                "conditions": "google>=0.5andoutlook>=0.5",
                "reports_waiting_time": 1.0
            }
        } 