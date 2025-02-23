from typing import List, Optional
from pydantic import Schema, Field
from datetime import datetime

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