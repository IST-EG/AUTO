"""
ERP Kernel — Domain Event Abstraction.

Defines the base DomainEvent contract and immutable metadata schema.
Automatically captures active TenantContext identity when available.
"""

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from app.kernel.tenant_context import TenantContext


@dataclass(frozen=True)
class DomainEvent:
    """
    Base class for all domain events across ERP business modules.
    Immutable, strongly-typed, and carries structured metadata.
    """
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = field(default="base.event")
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id: Optional[str] = field(default=None)
    correlation_id: Optional[str] = field(default=None)

    def __post_init__(self) -> None:
        # Automatically capture active TenantContext if tenant_id was not explicitly supplied
        if self.tenant_id is None:
            active_ctx = TenantContext.get()
            if active_ctx is not None:
                object.__setattr__(self, "tenant_id", active_ctx.tenant_id)

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializes event metadata and payload to a dictionary.
        """
        data = asdict(self)
        if isinstance(data.get("occurred_at"), datetime):
            data["occurred_at"] = data["occurred_at"].isoformat()
        return data
