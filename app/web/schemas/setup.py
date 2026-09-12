"""
Setup and bootstrap schemas.
"""

from pydantic import BaseModel, Field


class SetupStatusData(BaseModel):
    available: bool = Field(..., description="Whether first-run setup is currently available")


class BootstrapRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=50, description="Normalized username for initial OWNER")
    email: str = Field(..., min_length=5, max_length=255, description="Contact email address")
    password: str = Field(..., max_length=128, description="Strong password adhering to policy")
