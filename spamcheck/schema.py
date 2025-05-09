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
    search: Optional[str] = Field(None, description="Filter accounts by content")
    ignore_tags: Optional[List[str]] = Field(None, description="Don't include accounts with any of these tag titles (OR logic)")
    only_tags: Optional[List[str]] = Field(None, description="Only include accounts with at least one of these tag titles (OR logic)")
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
    platform: str

class PaginationMetaSchema(Schema):
    """Schema for pagination metadata"""
    total: int = Field(..., description="Total number of items")
    page: int = Field(..., description="Current page number")
    per_page: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total number of pages")

class ListSpamchecksResponseSchema(Schema):
    success: bool
    message: str
    data: List[SpamcheckDetailsSchema]
    meta: PaginationMetaSchema

class CreateSpamcheckBisonSchema(Schema):
    name: str
    user_organization_id: int
    accounts: Optional[List[str]] = Field(default=None, description="List of email accounts to check (required for specific account selection)")
    text_only: bool = Field(default=False)
    subject: str
    body: str
    scheduled_at: datetime
    recurring_days: Optional[int] = None
    weekdays: Optional[List[int]] = Field(None, description="List of weekdays (0=Monday, 6=Sunday) when this spamcheck should run")
    is_domain_based: bool = Field(default=False)
    conditions: Optional[str] = None
    reports_waiting_time: Optional[float] = Field(default=1.0)
    update_sending_limit: bool = Field(default=True, description="Whether to update sending limits in Bison API based on scores")
    account_selection_type: str = Field(default="specific", description="How to select accounts: 'specific', 'all', or 'tag_based'")
    include_tags: Optional[List[str]] = Field(default=None, description="Tags to include when account_selection_type is 'tag_based'")
    exclude_tags: Optional[List[str]] = Field(default=None, description="Tags to exclude when account_selection_type is 'tag_based'")
    campaign_copy_source_id: Optional[str] = Field(default=None, description="ID of campaign to copy email content from")

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
                "weekdays": [0, 3],  # Run on Monday and Thursday
                "is_domain_based": False,
                "conditions": "google>=0.5andoutlook>=0.5",
                "reports_waiting_time": 1.0,
                "update_sending_limit": True,
                "account_selection_type": "specific",
                "include_tags": ["important", "test"],
                "exclude_tags": ["blocked"],
                "campaign_copy_source_id": "12345"
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
    weekdays: Optional[List[int]] = Field(None, description="List of weekdays (0=Monday, 6=Sunday) when this spamcheck should run")
    is_domain_based: Optional[bool] = Field(None, description="Whether to filter accounts by domain")
    conditions: Optional[str] = Field(None, description="Conditions for sending")
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports")
    update_sending_limit: Optional[bool] = Field(None, description="Whether to update sending limits in Bison API based on scores")
    account_selection_type: Optional[str] = Field(None, description="How to select accounts: 'specific', 'all', or 'tag_based'")
    include_tags: Optional[List[str]] = Field(None, description="Tags to include when account_selection_type is 'tag_based'")
    exclude_tags: Optional[List[str]] = Field(None, description="Tags to exclude when account_selection_type is 'tag_based'")
    campaign_copy_source_id: Optional[str] = Field(None, description="ID of campaign to copy email content from")

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
                "weekdays": [0, 3],  # Run on Monday and Thursday
                "is_domain_based": False,
                "conditions": "google>=0.5andoutlook>=0.5",
                "reports_waiting_time": 1.0,
                "update_sending_limit": True,
                "account_selection_type": "tag_based",
                "include_tags": ["important", "active"],
                "exclude_tags": ["blocked", "paused"],
                "campaign_copy_source_id": "12345"
            }
        }

class SpamcheckBisonConfigurationSchema(Schema):
    """Schema for Bison spamcheck configuration"""
    domainBased: bool
    trackOpens: bool = Field(default=False)
    trackClicks: bool = Field(default=False)
    waitingTime: str
    googleInboxCriteria: str
    outlookInboxCriteria: str
    updateSendingLimit: bool = Field(default=True)
    weekdays: Optional[List[str]] = None
    text_only: bool = Field(default=False)
    conditions: Optional[str] = None

class SpamcheckBisonEmailContentSchema(Schema):
    """Schema for Bison spamcheck email content"""
    subject: str
    body: str

class SpamcheckBisonResultsSchema(Schema):
    """Schema for Bison spamcheck results summary"""
    googleScore: float
    outlookScore: float
    totalAccounts: int
    inboxedAccounts: int
    spamAccounts: int

class SpamcheckBisonDetailsSchema(Schema):
    """Schema for Bison spamcheck details response"""
    id: str
    name: str
    createdAt: str
    lastRunDate: str
    status: str
    scheduled_at: str
    recurring_days: Optional[int]
    account_selection_type: str
    include_tags: Optional[List[str]]
    exclude_tags: Optional[List[str]]
    campaign_copy_source_id: Optional[str]
    organization_id: int
    configuration: SpamcheckBisonConfigurationSchema
    emailContent: SpamcheckBisonEmailContentSchema
    results: SpamcheckBisonResultsSchema

class SpamcheckBisonDetailsResponseSchema(Schema):
    """Response schema for get Bison spamcheck details endpoint"""
    success: bool
    message: str
    data: SpamcheckBisonDetailsSchema

class BisonAccountReportSchema(Schema):
    """Schema for Bison account report"""
    id: str
    email: str
    googleScore: float
    outlookScore: float
    status: str
    reportLink: str
    createdAt: str

class BisonAccountsReportsResponseSchema(Schema):
    """Response schema for get Bison accounts reports endpoint"""
    success: bool
    message: str
    data: List[BisonAccountReportSchema]

class BisonAccountHistorySchema(Schema):
    """Schema for Bison account history data"""
    total_checks: int
    good_checks: int
    bad_checks: int
    
class BisonAccountLastCheckSchema(Schema):
    """Schema for Bison account last check data"""
    id: str
    date: str
    
class BisonAccountScoreHistorySchema(Schema):
    """Schema for Bison account score history data"""
    date: str
    google_score: float
    outlook_score: float
    status: str
    report_link: str

class BisonDomainAccountHistorySchema(Schema):
    """Schema for Bison domain account history data"""
    total_checks: int
    good_checks: int
    bad_checks: int

class BisonDomainAccountSchema(Schema):
    """Schema for Bison domain account data"""
    email: str
    google_score: float
    outlook_score: float
    status: str
    workspace: str
    last_check_date: str
    bounce_count: Optional[int] = None
    reply_count: Optional[int] = None
    emails_sent: Optional[int] = None
    history: BisonDomainAccountHistorySchema

class BisonDomainSummarySchema(Schema):
    """Schema for Bison domain summary data"""
    total_accounts: int
    avg_google_score: float
    avg_outlook_score: float
    inboxing_accounts: int
    resting_accounts: int
    total_checks: int
    good_checks: int
    bad_checks: int
    
class BisonAccountDetailsSchema(Schema):
    """Schema for Bison account details"""
    email: str
    domain: str
    sends_per_day: int
    google_score: float
    outlook_score: float
    status: str
    workspace: str
    last_check: BisonAccountLastCheckSchema
    reports_link: str
    history: BisonAccountHistorySchema
    bounce_count: Optional[int] = None
    reply_count: Optional[int] = None
    emails_sent: Optional[int] = None
    tags_list: Optional[List[str]] = None
    score_history: List[BisonAccountScoreHistorySchema] = []
    domain_accounts: List[BisonDomainAccountSchema] = []
    domain_summary: BisonDomainSummarySchema
    
class BisonAccountDetailsResponseSchema(Schema):
    """Response schema for get Bison account details endpoint"""
    success: bool
    message: str
    data: BisonAccountDetailsSchema

class CampaignCopyData(Schema):
    """Schema for campaign copy data"""
    subject: str = Field(..., description="Email subject from the campaign")
    body: str = Field(..., description="Email body from the campaign")
    campaign_id: str = Field(..., description="ID of the campaign")

class CampaignCopyResponse(Schema):
    """Schema for campaign copy response"""
    success: bool
    message: str
    data: Optional[CampaignCopyData] = None

class SpamcheckErrorLogSchema(Schema):
    """Schema for spamcheck error log"""
    id: int
    error_type: str
    provider: str
    error_message: str
    error_details: Optional[Dict[str, Any]] = None
    account_email: Optional[str] = None
    step: Optional[str] = None
    api_endpoint: Optional[str] = None
    status_code: Optional[int] = None
    workspace_id: Optional[str] = None
    tags: Optional[List[str]] = None
    created_at: datetime
    bison_spamcheck_id: Optional[int] = None
    spamcheck_id: Optional[int] = None
    spamcheck_name: Optional[str] = None
    user_id: int

class SpamcheckErrorLogResponseSchema(Schema):
    """Schema for spamcheck error log response"""
    success: bool
    message: str
    data: List[SpamcheckErrorLogSchema]
    meta: Dict[str, Any] 