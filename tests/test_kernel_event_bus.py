"""
Unit tests for ERP Kernel — EventBus and DomainEvent.

Verifies:
- Typed event publishing and handler reception
- Deterministic priority ordering
- Unsubscribe functionality
- Duplicate handler prevention
- Empty subscriber handling
- Exception isolation across handlers (all execute even if one fails)
- Aggregate error reporting (no silent swallowing)
- Fail-fast execution mode
- Automatic active TenantContext capture in DomainEvent
- Asynchronous handler dispatch via publish_async
- Base event polymorphic dispatch
"""

import asyncio
from dataclasses import dataclass
import pytest

from app.kernel.tenant_context import TenantContext
from app.kernel.events import DomainEvent
from app.kernel.event_bus import (
    EventBus,
    EventDispatchError,
    DuplicateHandlerError,
)


@dataclass(frozen=True)
class CustomerCreatedEvent(DomainEvent):
    event_type: str = "crm.customer.created"
    customer_id: str = ""
    email: str = ""


@dataclass(frozen=True)
class OrderPlacedEvent(DomainEvent):
    event_type: str = "sales.order.placed"
    order_id: str = ""
    amount: float = 0.0


def test_event_bus_subscribe_publish():
    """Verifies that an event is published and received by a subscribed handler."""
    bus = EventBus()
    received = []

    def on_customer(evt: CustomerCreatedEvent):
        received.append(evt)

    bus.subscribe(CustomerCreatedEvent, on_customer)
    evt = CustomerCreatedEvent(customer_id="cust-101", email="test@example.com")

    result = bus.publish(evt)
    assert len(received) == 1
    assert received[0].customer_id == "cust-101"
    assert result.handler_count == 1
    assert result.successful_count == 1
    assert result.failed_count == 0
    assert result.has_failures is False


def test_event_bus_multiple_handlers_priority_order():
    """Verifies that handlers execute in ascending priority order."""
    bus = EventBus()
    execution_order = []

    def handler_low(evt):
        execution_order.append("low_priority_100")

    def handler_high(evt):
        execution_order.append("high_priority_10")

    def handler_medium(evt):
        execution_order.append("medium_priority_50")

    # Subscribe in mixed order
    bus.subscribe(CustomerCreatedEvent, handler_low, priority=100)
    bus.subscribe(CustomerCreatedEvent, handler_high, priority=10)
    bus.subscribe(CustomerCreatedEvent, handler_medium, priority=50)

    evt = CustomerCreatedEvent(customer_id="cust-102")
    bus.publish(evt)

    assert execution_order == [
        "high_priority_10",
        "medium_priority_50",
        "low_priority_100",
    ]


def test_event_bus_unsubscribe():
    """Verifies that an unsubscribed handler is no longer invoked."""
    bus = EventBus()
    calls = []

    def handler(evt):
        calls.append(evt)

    bus.subscribe(CustomerCreatedEvent, handler)
    assert bus.get_handler_count(CustomerCreatedEvent) == 1

    removed = bus.unsubscribe(CustomerCreatedEvent, handler)
    assert removed is True
    assert bus.get_handler_count(CustomerCreatedEvent) == 0

    # Second unsubscribe returns False
    assert bus.unsubscribe(CustomerCreatedEvent, handler) is False

    evt = CustomerCreatedEvent(customer_id="cust-103")
    result = bus.publish(evt)
    assert len(calls) == 0
    assert result.handler_count == 0


def test_event_bus_duplicate_subscription_raises():
    """Verifies that subscribing the same handler twice raises DuplicateHandlerError."""
    bus = EventBus()

    def handler(evt):
        pass

    bus.subscribe(CustomerCreatedEvent, handler)
    with pytest.raises(DuplicateHandlerError, match="is already subscribed"):
        bus.subscribe(CustomerCreatedEvent, handler)


def test_event_bus_empty_subscribers():
    """Verifies that publishing an event with no handlers returns 0 executed with no errors."""
    bus = EventBus()
    evt = CustomerCreatedEvent(customer_id="cust-104")

    result = bus.publish(evt)
    assert result.handler_count == 0
    assert result.successful_count == 0
    assert result.failed_count == 0
    assert result.has_failures is False


def test_event_bus_handler_exception_isolation():
    """
    Verifies that a failure in one handler does NOT prevent subsequent handlers from executing,
    and all errors are collected and raised in EventDispatchError.
    """
    bus = EventBus()
    calls = []

    def handler_one(evt):
        calls.append("handler_one")

    def failing_handler(evt):
        calls.append("failing_handler")
        raise RuntimeError("Handler exploded intentionally")

    def handler_three(evt):
        calls.append("handler_three")

    bus.subscribe(CustomerCreatedEvent, handler_one, priority=10)
    bus.subscribe(CustomerCreatedEvent, failing_handler, priority=20)
    bus.subscribe(CustomerCreatedEvent, handler_three, priority=30)

    evt = CustomerCreatedEvent(customer_id="cust-105")

    # With raise_on_error=True (default), raises EventDispatchError
    with pytest.raises(EventDispatchError) as exc_info:
        bus.publish(evt)

    # All three handlers were still called!
    assert calls == ["handler_one", "failing_handler", "handler_three"]
    assert len(exc_info.value.errors) == 1
    assert "Handler exploded intentionally" in str(exc_info.value.errors[0])
    assert exc_info.value.result.successful_count == 2
    assert exc_info.value.result.failed_count == 1

    # With raise_on_error=False, does not raise but returns result with errors
    calls.clear()
    result = bus.publish(evt, raise_on_error=False)
    assert result.has_failures is True
    assert result.successful_count == 2
    assert result.failed_count == 1
    assert len(result.errors) == 1


def test_event_bus_fail_fast_mode():
    """Verifies that fail_fast=True halts execution immediately upon the first error."""
    bus = EventBus()
    calls = []

    def failing_handler(evt):
        calls.append("failing_handler")
        raise ValueError("Fail fast trigger")

    def subsequent_handler(evt):
        calls.append("subsequent_handler")

    bus.subscribe(CustomerCreatedEvent, failing_handler, priority=10)
    bus.subscribe(CustomerCreatedEvent, subsequent_handler, priority=20)

    evt = CustomerCreatedEvent(customer_id="cust-106")

    with pytest.raises(EventDispatchError):
        bus.publish(evt, fail_fast=True)

    assert calls == ["failing_handler"]
    assert "subsequent_handler" not in calls


def test_event_bus_automatic_tenant_metadata():
    """Verifies that DomainEvent automatically captures the active TenantContext."""
    ctx = TenantContext(tenant_id="tenant-captured-001")

    with TenantContext.scope(ctx):
        evt = CustomerCreatedEvent(customer_id="cust-107")
        assert evt.tenant_id == "tenant-captured-001"

    # Outside scope, tenant_id is None if not provided
    evt_outside = CustomerCreatedEvent(customer_id="cust-108")
    assert evt_outside.tenant_id is None

    # Explicit tenant_id overrides active context
    with TenantContext.scope(ctx):
        evt_override = CustomerCreatedEvent(customer_id="cust-109", tenant_id="tenant-explicit-999")
        assert evt_override.tenant_id == "tenant-explicit-999"


def test_event_bus_async_handler_dispatch():
    """Verifies that publish_async executes both sync and async handlers."""
    bus = EventBus()
    execution = []

    async def async_handler(evt: CustomerCreatedEvent):
        await asyncio.sleep(0.01)
        execution.append(f"async:{evt.customer_id}")

    def sync_handler(evt: CustomerCreatedEvent):
        execution.append(f"sync:{evt.customer_id}")

    bus.subscribe(CustomerCreatedEvent, sync_handler, priority=10)
    bus.subscribe(CustomerCreatedEvent, async_handler, priority=20)

    evt = CustomerCreatedEvent(customer_id="cust-110")

    async def _run():
        return await bus.publish_async(evt)

    result = asyncio.run(_run())

    assert result.successful_count == 2
    assert result.failed_count == 0
    assert execution == ["sync:cust-110", "async:cust-110"]


def test_event_bus_sync_publish_rejects_async_handler():
    """Verifies that calling synchronous publish() with an async handler reports a clear failure."""
    bus = EventBus()

    async def async_handler(evt):
        pass

    bus.subscribe(CustomerCreatedEvent, async_handler)
    evt = CustomerCreatedEvent(customer_id="cust-111")

    with pytest.raises(EventDispatchError) as exc_info:
        bus.publish(evt)

    assert "cannot be invoked via synchronous publish()" in str(exc_info.value.errors[0])


def test_event_bus_polymorphic_event_dispatch():
    """Verifies that a handler subscribed to DomainEvent receives all subclass instances."""
    bus = EventBus()
    received_types = []

    def general_audit_handler(evt: DomainEvent):
        received_types.append(evt.event_type)

    bus.subscribe(DomainEvent, general_audit_handler)

    bus.publish(CustomerCreatedEvent(customer_id="1"))
    bus.publish(OrderPlacedEvent(order_id="2", amount=99.0))

    assert received_types == ["crm.customer.created", "sales.order.placed"]


def test_event_bus_same_priority_deterministic_registration_order():
    """
    Gate 3 verification: Verifies deterministic registration-order execution
    when multiple handlers are subscribed with identical priority (e.g. priority=50).
    """
    bus = EventBus()
    order = []

    def handler_a(evt):
        order.append("A")

    def handler_b(evt):
        order.append("B")

    def handler_c(evt):
        order.append("C")

    # All three handlers registered with identical priority 50
    bus.subscribe(CustomerCreatedEvent, handler_a, priority=50)
    bus.subscribe(CustomerCreatedEvent, handler_b, priority=50)
    bus.subscribe(CustomerCreatedEvent, handler_c, priority=50)

    evt = CustomerCreatedEvent(customer_id="gate3-test")
    result = bus.publish(evt)

    assert result.successful_count == 3
    assert result.failed_count == 0
    assert order == ["A", "B", "C"], f"Expected ['A', 'B', 'C'] but got {order}"

    # Also test mixed priorities: priority 10 handlers vs priority 50 handlers
    order.clear()
    bus.clear()

    def handler_p10_first(evt):
        order.append("P10_1")

    def handler_p50_first(evt):
        order.append("P50_1")

    def handler_p10_second(evt):
        order.append("P10_2")

    def handler_p50_second(evt):
        order.append("P50_2")

    bus.subscribe(CustomerCreatedEvent, handler_p50_first, priority=50)
    bus.subscribe(CustomerCreatedEvent, handler_p10_first, priority=10)
    bus.subscribe(CustomerCreatedEvent, handler_p50_second, priority=50)
    bus.subscribe(CustomerCreatedEvent, handler_p10_second, priority=10)

    bus.publish(evt)
    assert order == ["P10_1", "P10_2", "P50_1", "P50_2"], f"Expected priority+order sort, got {order}"
