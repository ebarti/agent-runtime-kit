"""Vendor-independent runtime implementations used by tests and examples."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime_kit._control import RuntimeTaskController
from agent_runtime_kit._schema import resolve_structured_output
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    CancellationReceipt,
    RuntimeAvailability,
    RuntimeReadiness,
    TaskSupportReport,
    ToolCallAudit,
)
from agent_runtime_kit.events import (
    output_delta_event,
    safe_emit,
    task_completed_event,
    task_failed_event,
    task_started_event,
    tool_completed_event,
    tool_requested_event,
    vendor_turn_event,
)
from agent_runtime_kit.support import _validate_declared_task_support, require_task_support


class FakeAgentRuntime:
    """Small deterministic runtime for tests and local examples."""

    kind = AgentRuntimeKind.FAKE

    def __init__(
        self,
        *,
        output: str | None = None,
        capabilities: AgentCapabilities | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.capabilities = capabilities or AgentCapabilities(
            mcp_support=True,
            working_directory=True,
            session_resume=True,
            structured_output=True,
            streaming=False,
            tool_audit=True,
            cancellation=True,
            mcp_server_env=True,
        )
        self._output = output
        self._metadata = dict(metadata or {})
        self.cancelled_task_ids: set[str] = set()
        self._task_controller = RuntimeTaskController(self.kind)

    def availability(self) -> RuntimeAvailability:
        """Fake runtime is always available."""

        return RuntimeAvailability.ok(self.kind, package="agent-runtime-kit")

    def validate_task(self, task: AgentTask) -> TaskSupportReport:
        """Report unsupported fields without side effects."""

        return _validate_declared_task_support(self.kind, self.capabilities, task)

    async def check_readiness(self) -> RuntimeReadiness:
        """Fake runtime is deterministic and always ready to attempt."""

        availability = self.availability()
        return RuntimeReadiness.ready_to_attempt(
            self.kind,
            package=availability.package,
            version=availability.version,
            metadata=availability.metadata,
        )

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one deadline- and cancellation-controlled fake task."""

        return await self._task_controller.run(task, lambda: self._run_task(task))

    async def _run_task(self, task: AgentTask) -> AgentResult:
        """Return a deterministic result after validating capabilities."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            require_task_support(self.validate_task(task))
            output = self._output if self._output is not None else f"Fake result for: {task.goal}"
            parsed = {"output": output} if task.output_schema is not None else None
            parsed_available = parsed is not None
            structured_error: str | None = None
            if task.output_schema is not None:
                resolution = resolve_structured_output(
                    task.output_schema,
                    output,
                    sdk_label="Fake runtime",
                    native=parsed,
                    native_available=True,
                )
                parsed = resolution.value
                parsed_available = resolution.available
                structured_error = resolution.error
            tool_call = ToolCallAudit(
                tool_name="fake",
                arguments={"goal": task.goal},
                result_preview=output,
            )
            await safe_emit(
                task,
                output_delta_event(task, self.kind, text=output),
            )
            await safe_emit(
                task,
                tool_requested_event(
                    task,
                    self.kind,
                    tool_name="fake",
                    arguments=tool_call.arguments,
                ),
            )
            await safe_emit(task, tool_completed_event(task, self.kind, tool_call))
            await safe_emit(
                task,
                vendor_turn_event(
                    task,
                    self.kind,
                    payload={"runtime": "fake", "round": 1},
                    summary="fake runtime completed one turn",
                ),
            )
            result = AgentResult(
                output=output,
                finish_reason="failed" if structured_error is not None else "done",
                error=structured_error,
                parsed_output=parsed,
                parsed_output_available=parsed_available,
                tool_calls=(tool_call,),
                session_id=task.session_id or task.task_id,
                rounds=1,
                metadata={"task_id": task.task_id, **self._metadata},
            )
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise
        if result.error is not None:
            await safe_emit(task, task_failed_event(task, self.kind, error=result.error))
            return result
        await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> CancellationReceipt:
        """Record and request cancellation for an active fake task."""

        self.cancelled_task_ids.add(task_id)
        return await self._task_controller.cancel(task_id)

    async def aclose(self) -> None:
        """No-op: the fake runtime owns no vendor process."""

    async def __aenter__(self) -> FakeAgentRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()
