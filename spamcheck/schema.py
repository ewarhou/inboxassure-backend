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