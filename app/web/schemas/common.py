"""
Standardized API response models.
"""

from datetime import datetime, timezone
from typing import Generic, TypeVar, Optional, Dict, Any
from pydantic import BaseModel, Field

T = TypeVar("T")


class APIErrorDetails(BaseModel):
    code: str
    message: str
    details: Optional[Any] = None


class APIResponse(BaseModel, Generic[T]):
    """Standardized API JSON envelope for Web Control Center."""
    success: bool
    data: Optional[T] = None
    error: Optional[APIErrorDetails] = None
    meta: Dict[str, Any] = Field(default_factory=lambda: {
        "timestamp": datetime.now(timezone.utc).isoformat()
    })

    @classmethod
    def ok(cls, data: T, meta: Optional[Dict[str, Any]] = None) -> "APIResponse[T]":
        m = {"timestamp": datetime.now(timezone.utc).isoformat()}
        if meta:
            m.update(meta)
        return cls(success=True, data=data, error=None, meta=m)

    @classmethod
    def fail(cls, code: str, message: str, details: Optional[Any] = None, meta: Optional[Dict[str, Any]] = None) -> "APIResponse[Any]":
        m = {"timestamp": datetime.now(timezone.utc).isoformat()}
        if meta:
            m.update(meta)
        return cls(
            success=False,
            data=None,
            error=APIErrorDetails(code=code, message=message, details=details),
            meta=m
        )
