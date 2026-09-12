"""
Pydantic schemas for Message Templates.
"""

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class TemplateVersionDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    template_id: int
    version_number: int
    body: str
    created_by: Optional[str] = None
    created_at: datetime


class TemplateListDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    version_count: int
    latest_version: int
    latest_body: str
    created_at: datetime
    updated_at: datetime


class TemplateDetailDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: Optional[str] = None
    versions: List[TemplateVersionDTO]
    created_at: datetime
    updated_at: datetime


class TemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    body: str = Field(..., min_length=1)


class TemplateVersionCreateRequest(BaseModel):
    body: str = Field(..., min_length=1)


class TemplateValidateRequest(BaseModel):
    body: str = Field(..., min_length=1)


class TemplateValidateResponse(BaseModel):
    valid: bool
    variables: List[str]
    errors: List[str] = []


class TemplatePreviewRequest(BaseModel):
    body: str = Field(..., min_length=1)
    contact_name: Optional[str] = "John Doe"
    company: Optional[str] = "Acme Corp"
    city: Optional[str] = "Cairo"
    campaign_name: Optional[str] = "Sample Campaign"


class TemplatePreviewResponse(BaseModel):
    rendered_text: str
