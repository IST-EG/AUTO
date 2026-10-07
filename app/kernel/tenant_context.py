"""
ERP Kernel — Tenant Context Abstraction.

Provides request- and task-scoped tenant identity isolation using Python contextvars.
Thread-safe and asyncio-safe, with zero implicit global mutable state.
"""

from dataclasses import dataclass, field
from contextvars import ContextVar, Token
from contextlib import contextmanager
from functools import wraps
from typing import Optional, Dict, Any, Iterator, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class MissingTenantContextError(RuntimeError):
    """Raised when an operation requires an active TenantContext but none is bound."""
    pass


_current_tenant_var: ContextVar[Optional["TenantContext"]] = ContextVar(
    "current_tenant_context", default=None
)


@dataclass(frozen=True)
class TenantContext:
    """
    Immutable representation of an active tenant's execution context.
    """
    tenant_id: str
    name: str = "Default"
    slug: str = "default"
    is_system: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    SYSTEM_TENANT_ID: str = "00000000-0000-0000-0000-000000000001"

    @classmethod
    def create_system_context(cls, name: str = "System / Operator", metadata: Optional[Dict[str, Any]] = None) -> "TenantContext":
        """
        Creates the canonical system tenant context for background worker,
        daemon, or administrative operations.
        """
        return cls(
            tenant_id=cls.SYSTEM_TENANT_ID,
            name=name,
            slug="system",
            is_system=True,
            metadata=metadata or {},
        )

    @classmethod
    def get(cls) -> Optional["TenantContext"]:
        """
        Returns the current active TenantContext, or None if no context is bound.
        """
        return _current_tenant_var.get()

    @classmethod
    def require(cls) -> "TenantContext":
        """
        Returns the current active TenantContext.
        Raises MissingTenantContextError if unbound.
        """
        ctx = cls.get()
        if ctx is None:
            raise MissingTenantContextError(
                "Operation requires an active TenantContext, but none is currently bound."
            )
        return ctx

    @classmethod
    def get_current_tenant_id(cls) -> str:
        """
        Returns the active tenant_id string.
        Raises MissingTenantContextError if unbound.
        """
        return cls.require().tenant_id

    @classmethod
    def set(cls, context: "TenantContext") -> Token:
        """
        Sets the active TenantContext for the current execution context.
        Returns a Token for resetting.
        """
        if not isinstance(context, cls):
            raise TypeError(f"Expected TenantContext instance, got {type(context).__name__}")
        return _current_tenant_var.set(context)

    @classmethod
    def reset(cls, token: Token) -> None:
        """
        Resets the TenantContext to the value associated with the provided Token.
        """
        _current_tenant_var.reset(token)

    @classmethod
    @contextmanager
    def scope(cls, context: "TenantContext") -> Iterator["TenantContext"]:
        """
        Context manager that binds a TenantContext for the duration of a block,
        guaranteeing clean restoration upon exit even if exceptions occur.
        """
        token = cls.set(context)
        try:
            yield context
        finally:
            cls.reset(token)

    @classmethod
    def with_context(cls, context: "TenantContext") -> Callable[[F], F]:
        """
        Decorator that binds a TenantContext for the duration of the decorated function call.
        """
        def decorator(fn: F) -> F:
            @wraps(fn)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                with cls.scope(context):
                    return fn(*args, **kwargs)

            @wraps(fn)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with cls.scope(context):
                    return await fn(*args, **kwargs)

            import inspect
            if inspect.iscoroutinefunction(fn):
                return async_wrapper  # type: ignore[return-value]
            return sync_wrapper  # type: ignore[return-value]

        return decorator
