"""
Pydantic schemas for Contacts.
"""

from datetime import datetime
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field


class ContactListDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone_e164: str
    country_code: str
    company: Optional[str] = None
    city: Optional[str] = None
    consent_status: str
    contact_status: str
    messages_sent_count: int
    created_at: datetime


class ContactDetailDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone_e164: str
    country_code: str
    company: Optional[str] = None
    city: Optional[str] = None
    consent_status: str
    contact_status: str
    is_duplicate: bool
    messages_sent_count: int
    last_contacted_at: Optional[datetime] = None
    opted_in_at: Optional[datetime] = None
    opted_out_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class ContactCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    phone_number: str = Field(..., min_length=1)
    country_code: str = Field(..., min_length=1)
    company: Optional[str] = None
    city: Optional[str] = None
    consent_status: str = Field(default="pending", pattern="^(pending|opted_in|opted_out)$")


class ContactUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1)
    company: Optional[str] = None
    city: Optional[str] = None
    consent_status: Optional[str] = Field(default=None, pattern="^(pending|opted_in|opted_out)$")
    contact_status: Optional[str] = Field(default=None, pattern="^(active|inactive|duplicate)$")


class CSVImportDryRunResponse(BaseModel):
    total_rows: int
    valid_count: int
    invalid_phone_count: int
    duplicate_count: int
    missing_fields_count: int
    errors: List[str] = []
    preview: List[Dict[str, Any]] = []


class CSVImportCommitResponse(BaseModel):
    total_rows: int
    imported: int
    skipped_duplicate: int
    skipped_invalid_phone: int
    skipped_missing_fields: int
    errors: List[str] = []
