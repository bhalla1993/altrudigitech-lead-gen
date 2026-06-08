from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.sql import func
from .db import Base

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True, index=True)
    business_name = Column(String, nullable=True)
    website_url = Column(String, nullable=False)
    score = Column(Integer, nullable=True)
    reason = Column(Text, nullable=True)
    explanation = Column(Text, nullable=True)
    suggestion = Column(Text, nullable=True)
    screenshot_desktop = Column(String, nullable=True)
    screenshot_mobile = Column(String, nullable=True)
    features_json = Column(Text, nullable=True)
    # Lighthouse / audit metadata
    last_audit_path = Column(String, nullable=True)
    audit_status = Column(String, nullable=True)
    audit_completed_at = Column(DateTime(timezone=True), nullable=True)
    contacted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class OutreachLog(Base):
    __tablename__ = "outreach_logs"
    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    email_sent = Column(Text, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now())
