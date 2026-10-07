"""
Unit tests for ERP Kernel — TenantContext.

Verifies:
- Set / Get / Require semantics
- Missing context error handling
- Scope context manager restoration (normal and exception)
- Nested scope restoration
- Decorator support (sync and async)
- System tenant representation
- Concurrent OS thread isolation
- Concurrent asyncio task isolation
"""

import asyncio
import threading
import pytest
from app.kernel.tenant_context import (
    TenantContext,
    MissingTenantContextError,
)


def test_tenant_context_set_get():
    """Verifies basic setting, retrieval, and resetting of TenantContext."""
    ctx = TenantContext(tenant_id="tenant-alpha-123", name="Alpha Corp", slug="alpha")
    assert TenantContext.get() is None

    token = TenantContext.set(ctx)
    try:
        assert TenantContext.get() == ctx
        assert TenantContext.require() == ctx
        assert TenantContext.get_current_tenant_id() == "tenant-alpha-123"
    finally:
        TenantContext.reset(token)

    assert TenantContext.get() is None


def test_tenant_context_missing_raises():
    """Verifies that require() and get_current_tenant_id() raise when no context is active."""
    assert TenantContext.get() is None

    with pytest.raises(MissingTenantContextError) as exc_info:
        TenantContext.require()
    assert "Operation requires an active TenantContext" in str(exc_info.value)

    with pytest.raises(MissingTenantContextError):
        TenantContext.get_current_tenant_id()


def test_tenant_context_scope_context_manager():
    """Verifies that scope() sets and cleanly resets TenantContext."""
    ctx = TenantContext(tenant_id="tenant-beta-456", name="Beta LLC", slug="beta")

    assert TenantContext.get() is None
    with TenantContext.scope(ctx) as active_ctx:
        assert active_ctx == ctx
        assert TenantContext.get() == ctx
        assert TenantContext.get_current_tenant_id() == "tenant-beta-456"

    assert TenantContext.get() is None


def test_tenant_context_scope_restoration_on_exception():
    """Verifies that scope() restores previous context even if an exception occurs."""
    ctx = TenantContext(tenant_id="tenant-gamma-789")

    assert TenantContext.get() is None
    with pytest.raises(ValueError, match="Intentional test error"):
        with TenantContext.scope(ctx):
            assert TenantContext.get() == ctx
            raise ValueError("Intentional test error")

    assert TenantContext.get() is None


def test_tenant_context_nested_scopes():
    """Verifies that nested scope() calls cleanly restore outer context upon exit."""
    outer = TenantContext(tenant_id="outer-tenant")
    inner = TenantContext(tenant_id="inner-tenant")

    assert TenantContext.get() is None
    with TenantContext.scope(outer):
        assert TenantContext.get_current_tenant_id() == "outer-tenant"

        with TenantContext.scope(inner):
            assert TenantContext.get_current_tenant_id() == "inner-tenant"

        assert TenantContext.get_current_tenant_id() == "outer-tenant"

    assert TenantContext.get() is None


def test_tenant_context_decorator_sync():
    """Verifies @TenantContext.with_context on synchronous functions."""
    ctx = TenantContext(tenant_id="decorator-tenant")

    @TenantContext.with_context(ctx)
    def my_action(val: int) -> int:
        assert TenantContext.get_current_tenant_id() == "decorator-tenant"
        return val * 2

    assert TenantContext.get() is None
    result = my_action(21)
    assert result == 42
    assert TenantContext.get() is None


def test_tenant_context_decorator_async():
    """Verifies @TenantContext.with_context on asynchronous coroutines."""
    ctx = TenantContext(tenant_id="async-decorator-tenant")

    @TenantContext.with_context(ctx)
    async def my_async_action(val: int) -> int:
        await asyncio.sleep(0.01)
        assert TenantContext.get_current_tenant_id() == "async-decorator-tenant"
        return val + 10

    async def _run():
        assert TenantContext.get() is None
        result = await my_async_action(5)
        assert result == 15
        assert TenantContext.get() is None

    asyncio.run(_run())


def test_tenant_context_system_tenant():
    """Verifies canonical system tenant creation and constants."""
    sys_ctx = TenantContext.create_system_context(name="Worker Engine")
    assert sys_ctx.tenant_id == TenantContext.SYSTEM_TENANT_ID
    assert sys_ctx.tenant_id == "00000000-0000-0000-0000-000000000001"
    assert sys_ctx.is_system is True
    assert sys_ctx.name == "Worker Engine"
    assert sys_ctx.slug == "system"


def test_tenant_context_thread_isolation():
    """Verifies strict isolation across multiple concurrent OS threads."""
    errors = []

    def worker(tenant_num: int):
        try:
            expected_id = f"thread-tenant-{tenant_num}"
            ctx = TenantContext(tenant_id=expected_id)

            assert TenantContext.get() is None
            with TenantContext.scope(ctx):
                import time
                time.sleep(0.02)
                assert TenantContext.get_current_tenant_id() == expected_id

            assert TenantContext.get() is None
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Thread isolation errors encountered: {errors}"
    assert TenantContext.get() is None


def test_tenant_context_async_task_isolation():
    """Verifies strict isolation across multiple concurrent asyncio tasks."""
    errors = []

    async def async_worker(task_num: int):
        try:
            expected_id = f"task-tenant-{task_num}"
            ctx = TenantContext(tenant_id=expected_id)

            assert TenantContext.get() is None
            with TenantContext.scope(ctx):
                await asyncio.sleep(0.02)
                assert TenantContext.get_current_tenant_id() == expected_id

            assert TenantContext.get() is None
        except Exception as e:
            errors.append(e)

    async def _run():
        tasks = [async_worker(i) for i in range(10)]
        await asyncio.gather(*tasks)

    asyncio.run(_run())

    assert len(errors) == 0, f"Async task isolation errors encountered: {errors}"
    assert TenantContext.get() is None


def test_tenant_context_async_task_lifecycle_leakage_resilience():
    """
    Gate 4 verification: Verifies that TenantContext cannot leak between concurrent
    asyncio tasks even when:
    - Task 1 completes early
    - Task 2 raises an unhandled exception
    - Task 3 continues running after Task 1 completes and Task 2 fails
    """
    task_events = []
    task_errors = []

    async def task_early():
        try:
            ctx1 = TenantContext(tenant_id="tenant-early-exit")
            with TenantContext.scope(ctx1):
                assert TenantContext.get_current_tenant_id() == "tenant-early-exit"
                await asyncio.sleep(0.01)
                assert TenantContext.get_current_tenant_id() == "tenant-early-exit"
                task_events.append("early_done")
            assert TenantContext.get() is None
        except Exception as e:
            task_errors.append(("task_early", e))

    async def task_failing():
        try:
            ctx2 = TenantContext(tenant_id="tenant-failing")
            with TenantContext.scope(ctx2):
                assert TenantContext.get_current_tenant_id() == "tenant-failing"
                await asyncio.sleep(0.02)
                assert TenantContext.get_current_tenant_id() == "tenant-failing"
                task_events.append("failing_about_to_raise")
                raise RuntimeError("Task 2 intentional failure")
        except RuntimeError as e:
            assert str(e) == "Task 2 intentional failure"
            assert TenantContext.get() is None, "Context must be None after scope exit even on error"
            task_events.append("failing_caught")
        except Exception as e:
            task_errors.append(("task_failing", e))

    async def task_surviving():
        try:
            ctx3 = TenantContext(tenant_id="tenant-surviving-long")
            with TenantContext.scope(ctx3):
                assert TenantContext.get_current_tenant_id() == "tenant-surviving-long"
                # Sleep long enough for task_early and task_failing to finish
                await asyncio.sleep(0.06)
                # Verify that despite task 1 completing and task 2 failing, this task's context is untouched
                assert TenantContext.get_current_tenant_id() == "tenant-surviving-long"
                assert "early_done" in task_events
                assert "failing_caught" in task_events
                task_events.append("surviving_done")
            assert TenantContext.get() is None
        except Exception as e:
            task_errors.append(("task_surviving", e))

    async def _orchestrate():
        assert TenantContext.get() is None
        await asyncio.gather(task_early(), task_failing(), task_surviving())
        assert TenantContext.get() is None

    asyncio.run(_orchestrate())

    assert len(task_errors) == 0, f"Unexpected task errors: {task_errors}"
    assert task_events == [
        "early_done",
        "failing_about_to_raise",
        "failing_caught",
        "surviving_done",
    ]
    assert TenantContext.get() is None
