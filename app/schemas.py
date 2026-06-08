from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime


class ScanRequest(BaseModel):
    website_url: HttpUrl
    business_name: Optional[str] = None


class BatchScanRequest(BaseModel):
    website_urls: List[HttpUrl]
    business_name: Optional[str] = None


class BatchScanResponse(BaseModel):
    accepted: int


class LeadResponse(BaseModel):
    id: int
    business_name: Optional[str]
    website_url: str
    score: Optional[int]
    reason: Optional[str]
    explanation: Optional[str]
    suggestion: Optional[str]
    screenshot_desktop: Optional[str]
    screenshot_mobile: Optional[str]
    features_json: Optional[str]
    last_audit_path: Optional[str]
    audit_status: Optional[str]
    audit_completed_at: Optional[datetime]
    contacted: bool
    created_at: datetime

    class Config:
        orm_mode = True
