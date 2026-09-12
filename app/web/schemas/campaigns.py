"""
Pydantic schemas for Campaigns.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class CampaignStatsDTO(BaseModel):
    total_contacts: int = 0
    pending: int = 0
    eligible: int = 0
    excluded: int = 0
    queued: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    completion_percentage: float = 0.0


class CampaignListDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    daily_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    batch_size: int
    batch_pause_seconds: int
    template_version_id: Optional[int] = None
    total_contacts: int = 0
    created_at: datetime
    updated_at: datetime


class CampaignDetailDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    message_template: str
    template_version_id: Optional[int] = None
    daily_limit: int
    min_delay_seconds: int
    max_delay_seconds: int
    batch_size: int
    batch_pause_seconds: int
    created_at: datetime
    updated_at: datetime
    stats: CampaignStatsDTO


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    message_template: Optional[str] = None
    template_id: Optional[int] = None
    template_version_id: Optional[int] = None
    daily_limit: int = Field(default=100, ge=1)
    min_delay_seconds: int = Field(default=10, ge=1)
    max_delay_seconds: int = Field(default=30, ge=1)
    batch_size: int = Field(default=20, ge=1)
    batch_pause_seconds: int = Field(default=60, ge=0)


class CampaignUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    message_template: Optional[str] = Field(default=None, min_length=1)
    template_version_id: Optional[int] = None
    daily_limit: Optional[int] = Field(default=None, ge=1)
    min_delay_seconds: Optional[int] = Field(default=None, ge=1)
    max_delay_seconds: Optional[int] = Field(default=None, ge=1)
    batch_size: Optional[int] = Field(default=None, ge=1)
    batch_pause_seconds: Optional[int] = Field(default=None, ge=0)


class CampaignStatusTransitionRequest(BaseModel):
    status: str = Field(..., pattern="^(DRAFT|SCHEDULED|RUNNING|PAUSED|COMPLETED|CANCELLED)$")


class CampaignContactDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    campaign_id: int
    contact_id: int
    contact_name: str
    contact_phone: str
    contact_company: Optional[str] = None
    contact_city: Optional[str] = None
    status: str
    exclusion_reason: Optional[str] = None
    enqueued_at: Optional[datetime] = None
    sent_at: Optional[datetime] = None


class CampaignAddContactsRequest(BaseModel):
    contact_ids: List[int] = Field(..., min_length=1)


class CampaignAddContactsResponse(BaseModel):
    total: int
    added: int
    excluded: int
    duplicates: int
