from typing import List, Optional
from datetime import datetime
from ninja import Schema, Field
from uuid import UUID

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
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports (e.g., 0.5 for 30min, 3 for 3h). Default is 1h")

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
    reports_waiting_time: Optional[float] = Field(None, description="Time in hours to wait before generating reports (e.g., 0.5 for 30min, 3 for 3h). Default is 1h")

class LaunchSpamcheckSchema(Schema):
    spamcheck_id: int = Field(..., description="ID of the spamcheck to launch")
    is_test: bool = Field(default=False, description="Whether this is a test launch") 