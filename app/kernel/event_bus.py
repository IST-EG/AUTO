"""
ERP Kernel — In-Process Domain Event Bus.

Delivers typed, in-process pub/sub event dispatching with:
- Deterministic priority ordering.
- Exception isolation and aggregation across handlers.
- Both synchronous and asynchronous execution support.
- Zero external broker dependencies and zero background threads.
"""

import time
import inspect
import logging
import threading
from dataclasses import dataclass, field
from typing import Type, Callable, List, Dict, Optional, Any, TypeVar

from app.kernel.events import DomainEvent

logger = logging.getLogger("kernel.event_bus")

T = TypeVar("T", bound=DomainEvent)


class EventDispatchError(RuntimeError):
    """
    Raised when one or more event handlers encounter an exception during dispatch.
    Aggregates all handler exceptions for full observability without silent swallowing.
    """
    def __init__(self, message: str, errors: List[Exception], result: "EventDispatchResult"):
        super().__init__(message)
        self.errors = errors
        self.result = result


class DuplicateHandlerError(ValueError):
    """Raised when an identical handler is registered more than once for the same event class."""
    pass


@dataclass
class HandlerExecution:
    """Records the outcome of an individual handler execution."""
    handler_name: str
    success: bool
    execution_time_ms: float
    exception: Optional[Exception] = None


@dataclass
class EventDispatchResult:
    """Summarizes the outcome of dispatching an event to all registered handlers."""
    event_id: str
    event_type: str
    handler_count: int
    successful_count: int
    failed_count: int
    executions: List[HandlerExecution] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return self.failed_count > 0

    @property
    def errors(self) -> List[Exception]:
        return [ex.exception for ex in self.executions if ex.exception is not None]


@dataclass
class _Subscription:
    handler: Callable
    priority: int
    is_async: bool
    order: int = 0


class EventBus:
    """
    Lightweight, thread-safe, in-process domain event bus.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._subscribers: Dict[Type[DomainEvent], List[_Subscription]] = {}
        self._counter: int = 0

    def subscribe(
        self,
        event_cls: Type[DomainEvent],
        handler: Callable,
        priority: int = 100,
    ) -> None:
        """
        Subscribes a callable handler to a specific DomainEvent class.

        Args:
            event_cls: DomainEvent subclass to listen for.
            handler: Callable (sync or async) taking the event instance as its sole argument.
            priority: Integer priority (lower executes earlier, default 100).
                      Handlers with identical priority execute in registration order.
        """
        if not issubclass(event_cls, DomainEvent):
            raise TypeError(f"event_cls must be a subclass of DomainEvent, got {event_cls}")
        if not callable(handler):
            raise TypeError(f"handler must be callable, got {type(handler)}")

        with self._lock:
            if event_cls not in self._subscribers:
                self._subscribers[event_cls] = []

            # Prevent duplicate handler registration
            for sub in self._subscribers[event_cls]:
                if sub.handler == handler:
                    raise DuplicateHandlerError(
                        f"Handler {getattr(handler, '__name__', str(handler))} is already "
                        f"subscribed to {event_cls.__name__}."
                    )

            is_async = inspect.iscoroutinefunction(handler)
            self._counter += 1
            self._subscribers[event_cls].append(
                _Subscription(handler=handler, priority=priority, is_async=is_async, order=self._counter)
            )
            # Maintain deterministic ordering by ascending priority, then registration order
            self._subscribers[event_cls].sort(key=lambda s: (s.priority, s.order))

    def unsubscribe(self, event_cls: Type[DomainEvent], handler: Callable) -> bool:
        """
        Removes a subscribed handler from an event class.
        Returns True if the handler was found and removed, False otherwise.
        """
        with self._lock:
            if event_cls not in self._subscribers:
                return False

            initial_len = len(self._subscribers[event_cls])
            self._subscribers[event_cls] = [
                s for s in self._subscribers[event_cls] if s.handler != handler
            ]
            return len(self._subscribers[event_cls]) < initial_len

    def get_handler_count(self, event_cls: Optional[Type[DomainEvent]] = None) -> int:
        """Returns the number of subscribed handlers for a specific event class, or total."""
        with self._lock:
            if event_cls is not None:
                return len(self._subscribers.get(event_cls, []))
            return sum(len(subs) for subs in self._subscribers.values())

    def clear(self) -> None:
        """Clears all subscriptions. Primarily used for test isolation."""
        with self._lock:
            self._subscribers.clear()

    def publish(
        self,
        event: DomainEvent,
        fail_fast: bool = False,
        raise_on_error: bool = True,
    ) -> EventDispatchResult:
        """
        Synchronously dispatches an event to all registered handlers in priority order.

        Args:
            event: The DomainEvent instance to publish.
            fail_fast: If True, halts execution on the first handler failure.
            raise_on_error: If True and any handler fails, raises EventDispatchError.

        Returns:
            EventDispatchResult detailing executions and outcomes.
        """
        if not isinstance(event, DomainEvent):
            raise TypeError(f"Expected DomainEvent instance, got {type(event).__name__}")

        with self._lock:
            # Match handlers registered for this exact class and its base classes
            subscriptions: List[_Subscription] = []
            for cls, subs in self._subscribers.items():
                if isinstance(event, cls):
                    subscriptions.extend(subs)

        # Sort combined subscriptions deterministically: priority ascending, then registration order
        subscriptions.sort(key=lambda s: (s.priority, s.order))

        result = EventDispatchResult(
            event_id=event.event_id,
            event_type=event.event_type,
            handler_count=len(subscriptions),
            successful_count=0,
            failed_count=0,
            executions=[],
        )

        if not subscriptions:
            return result

        errors: List[Exception] = []

        for sub in subscriptions:
            handler_name = getattr(sub.handler, "__name__", str(sub.handler))
            t0 = time.perf_counter()

            if sub.is_async:
                err = RuntimeError(
                    f"Async handler '{handler_name}' cannot be invoked via synchronous publish(). "
                    f"Use publish_async() instead."
                )
                elapsed_ms = (time.perf_counter() - t0) * 1000
                result.failed_count += 1
                result.executions.append(
                    HandlerExecution(handler_name=handler_name, success=False, execution_time_ms=elapsed_ms, exception=err)
                )
                errors.append(err)
                if fail_fast:
                    break
                continue

            try:
                sub.handler(event)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                result.successful_count += 1
                result.executions.append(
                    HandlerExecution(handler_name=handler_name, success=True, execution_time_ms=elapsed_ms)
                )
            except Exception as ex:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.error(
                    f"Handler '{handler_name}' failed processing event {event.event_type} ({event.event_id}): {ex}",
                    exc_info=True,
                )
                result.failed_count += 1
                result.executions.append(
                    HandlerExecution(handler_name=handler_name, success=False, execution_time_ms=elapsed_ms, exception=ex)
                )
                errors.append(ex)
                if fail_fast:
                    break

        if errors and raise_on_error:
            raise EventDispatchError(
                f"Event {event.event_type} ({event.event_id}) dispatch encountered {len(errors)} failure(s).",
                errors=errors,
                result=result,
            )

        return result

    async def publish_async(
        self,
        event: DomainEvent,
        fail_fast: bool = False,
        raise_on_error: bool = True,
    ) -> EventDispatchResult:
        """
        Asynchronously dispatches an event to all registered handlers (sync and async) in priority order.
        """
        if not isinstance(event, DomainEvent):
            raise TypeError(f"Expected DomainEvent instance, got {type(event).__name__}")

        with self._lock:
            subscriptions: List[_Subscription] = []
            for cls, subs in self._subscribers.items():
                if isinstance(event, cls):
                    subscriptions.extend(subs)

        # Sort combined subscriptions deterministically: priority ascending, then registration order
        subscriptions.sort(key=lambda s: (s.priority, s.order))

        result = EventDispatchResult(
            event_id=event.event_id,
            event_type=event.event_type,
            handler_count=len(subscriptions),
            successful_count=0,
            failed_count=0,
            executions=[],
        )

        if not subscriptions:
            return result

        errors: List[Exception] = []

        for sub in subscriptions:
            handler_name = getattr(sub.handler, "__name__", str(sub.handler))
            t0 = time.perf_counter()

            try:
                if sub.is_async:
                    await sub.handler(event)
                else:
                    sub.handler(event)

                elapsed_ms = (time.perf_counter() - t0) * 1000
                result.successful_count += 1
                result.executions.append(
                    HandlerExecution(handler_name=handler_name, success=True, execution_time_ms=elapsed_ms)
                )
            except Exception as ex:
                elapsed_ms = (time.perf_counter() - t0) * 1000
                logger.error(
                    f"Handler '{handler_name}' failed processing async event {event.event_type} ({event.event_id}): {ex}",
                    exc_info=True,
                )
                result.failed_count += 1
                result.executions.append(
                    HandlerExecution(handler_name=handler_name, success=False, execution_time_ms=elapsed_ms, exception=ex)
                )
                errors.append(ex)
                if fail_fast:
                    break

        if errors and raise_on_error:
            raise EventDispatchError(
                f"Event {event.event_type} ({event.event_id}) dispatch encountered {len(errors)} failure(s).",
                errors=errors,
                result=result,
            )

        return result


# Global in-process event bus singleton
default_event_bus = EventBus()
