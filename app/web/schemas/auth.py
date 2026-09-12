"""
Authentication schemas.
"""

from typing import Optional
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=1, max_length=128)


class UserDTO(BaseModel):
    id: str
    username: str
    email: str
    role: str
    is_active: bool = True
    last_login_at: Optional[str] = None
