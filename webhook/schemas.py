from ninja import Schema
import uuid
from datetime import datetime
from typing import Optional, List

class WebhookUrlSchema(Schema):
    webhook_url: str 

class BisonBounceSchema(Schema):
    id: int
    # webhook_id: Optional[uuid.UUID] # Maybe exclude the FK object itself unless needed
    workspace_bison_id: Optional[int]
    workspace_name: Optional[str]
    email_subject: Optional[str]
    email_body: Optional[str]
    lead_email: str
    campaign_bison_id: Optional[int]
    campaign_name: Optional[str]
    sender_bison_id: Optional[int]
    sender_email: str
    domain: Optional[str]
    tags: Optional[List[str]]
    bounce_reply: Optional[str]
    bounce_bucket: Optional[str]
    bounce_code: Optional[str]
    bounce_reply_url: Optional[str]
    created_at: datetime 