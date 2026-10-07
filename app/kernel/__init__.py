"""
ERP Application Kernel.

Level 0 foundation package providing:
- TenantContext: Request- and task-scoped tenant isolation.
- DomainEvent: Strongly-typed immutable domain event contracts.
- EventBus: Lightweight, in-process pub/sub event dispatcher.
"""

from app.kernel.tenant_context import (
    TenantContext,
    MissingTenantContextError,
)
from app.kernel.events import (
    DomainEvent,
)
from app.kernel.event_bus import (
    EventBus,
    default_event_bus,
    EventDispatchResult,
    HandlerExecution,
    EventDispatchError,
    DuplicateHandlerError,
)

__all__ = [
    "TenantContext",
    "MissingTenantContextError",
    "DomainEvent",
    "EventBus",
    "default_event_bus",
    "EventDispatchResult",
    "HandlerExecution",
    "EventDispatchError",
    "DuplicateHandlerError",
]
