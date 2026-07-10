"""Portable task deadline and cancellation control.

Cancellation is cooperative at the Python coroutine boundary.  A successful
receipt means that cancellation was requested; it cannot promise that vendor
tools have rolled back side effects that already happened.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, TypeVar

from agent_runtime_kit._errors import AgentRuntimeError, AgentTaskTimeoutError
from agent_runtime_kit._types import (
    AgentRuntimeKind,
    AgentTask,
    CancellationDisposition,
    CancellationReceipt,
    FinishReason,
)
from agent_runtime_kit.events import safe_emit, task_failed_event

_T = TypeVar("_T")
_OPERATION_CLEANUP_TIMEOUT = 5.0


@dataclass
class _ActiveRun:
    task: asyncio.Task[Any]
    cancellation_requested: bool = False


class RuntimeTaskController:
    """Track active runs for one runtime and enforce their deadlines."""

    def __init__(self, kind: AgentRuntimeKind | str) -> None:
        self.kind = AgentRuntimeKind.coerce(kind)
        self._active: dict[str, _ActiveRun] = {}

    async def run(
        self,
        task: AgentTask,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Run ``operation`` under task-id registration and deadline control."""

        current = asyncio.current_task()
        if current is None:  # pragma: no cover - asyncio always supplies one here
            raise AgentRuntimeError("task control requires an active asyncio task")
        # These registry operations deliberately contain no await.  asyncio tasks
        # share one event-loop thread, so the check-and-set is atomic without a
        # cancellable lock acquisition.  The same is true of cleanup below.
        existing = self._active.get(task.task_id)
        if existing is not None:
            raise AgentRuntimeError(
                f"task_id {task.task_id!r} is already active for {self.kind}"
            )
        self._active[task.task_id] = _ActiveRun(current)

        try:
            return await self._run_with_deadline(task, operation)
        finally:
            active = self._active.get(task.task_id)
            if active is not None and active.task is current:
                del self._active[task.task_id]

    async def cancel(
        self,
        task_id: str,
        *,
        _expected_task: asyncio.Task[Any] | None = None,
    ) -> CancellationReceipt:
        """Request cancellation of an active coroutine without blocking on it."""

        active = self._active.get(task_id)
        if active is None or (
            _expected_task is not None and active.task is not _expected_task
        ):
            return self._receipt(task_id, CancellationDisposition.NOT_ACTIVE)
        if active.cancellation_requested:
            return self._receipt(task_id, CancellationDisposition.ALREADY_REQUESTED)
        active.cancellation_requested = True
        accepted = active.task.cancel()
        if not accepted:
            return self._receipt(
                task_id,
                CancellationDisposition.FAILED,
                "the active asyncio task had already completed",
            )
        return self._receipt(task_id, CancellationDisposition.REQUESTED)

    async def _run_with_deadline(
        self,
        task: AgentTask,
        operation: Callable[[], Awaitable[_T]],
    ) -> _T:
        deadline = task.deadline
        if deadline is None:
            try:
                return await operation()
            except asyncio.CancelledError:
                await self._emit_interrupted(task)
                raise

        remaining = (deadline - datetime.now(tz=timezone.utc)).total_seconds()
        if remaining <= 0:
            # An already-expired task never starts, including its started event.
            # Emit the terminal first so cancellation of a blocking observability
            # sink cannot strand the lifecycle after a misleading start.
            await self._emit_timed_out(task, deadline)
            raise AgentTaskTimeoutError(self.kind, task.task_id, deadline)

        operation_task: asyncio.Future[_T] = asyncio.ensure_future(operation())
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                {operation_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if operation_task in done:
                return await operation_task

            # Transition before emitting: cancellation of a slow sink must never
            # turn one timeout into a second, contradictory interrupted terminal.
            timed_out = True
            await _cancel_and_settle(operation_task)
            await self._emit_timed_out(task, deadline)
            raise AgentTaskTimeoutError(self.kind, task.task_id, deadline)
        except asyncio.CancelledError:
            await _cancel_and_settle(operation_task)
            if not timed_out:
                await self._emit_interrupted(task)
            raise

    async def _emit_interrupted(self, task: AgentTask) -> None:
        await safe_emit(
            task,
            task_failed_event(
                task,
                self.kind,
                error="task cancellation requested",
                finish_reason=FinishReason.INTERRUPTED.value,
            ),
        )

    async def _emit_timed_out(self, task: AgentTask, deadline: datetime) -> None:
        await safe_emit(
            task,
            task_failed_event(
                task,
                self.kind,
                error=f"task exceeded deadline {deadline.isoformat()}",
                finish_reason=FinishReason.TIMED_OUT.value,
            ),
        )

    def _receipt(
        self,
        task_id: str,
        disposition: CancellationDisposition,
        message: str | None = None,
    ) -> CancellationReceipt:
        return CancellationReceipt(
            task_id=task_id,
            kind=self.kind,
            disposition=disposition,
            message=message,
        )


async def run_legacy_with_deadline(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    operation: Callable[[], Awaitable[_T]],
) -> _T:
    """Enforce deadlines for third-party runtimes without native task control."""

    # A fresh controller is intentional: AgentKit owns cancellation tracking for
    # legacy runtimes, while this helper owns only this invocation's deadline and
    # terminal event semantics.
    controller = RuntimeTaskController(kind)
    return await controller.run(task, operation)


async def _cancel_and_settle(operation: asyncio.Future[Any]) -> None:
    """Give a cancelled operation bounded, repeat-cancel-safe cleanup time."""

    operation.cancel()
    deadline = asyncio.get_running_loop().time() + _OPERATION_CLEANUP_TIMEOUT
    while not operation.done():
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            operation.cancel()
            operation.add_done_callback(_consume_operation_result)
            return
        try:
            await asyncio.wait_for(asyncio.shield(operation), timeout=remaining)
        except asyncio.CancelledError:
            continue
        except asyncio.TimeoutError:
            operation.cancel()
            operation.add_done_callback(_consume_operation_result)
            return
        except Exception:
            break
    _consume_operation_result(operation)


def _consume_operation_result(operation: asyncio.Future[Any]) -> None:
    try:
        operation.exception()
    except BaseException:
        return
