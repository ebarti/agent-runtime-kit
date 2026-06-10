"""Dependency-free runtime implementations used by tests and examples."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    RuntimeAvailability,
    ToolCallAudit,
)


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
        )
        self._output = output
        self._metadata = dict(metadata or {})
        self.cancelled_task_ids: set[str] = set()

    def availability(self) -> RuntimeAvailability:
        """Fake runtime is always available."""

        return RuntimeAvailability.ok(self.kind, package="agent-runtime-kit")

    async def run(self, task: AgentTask) -> AgentResult:
        """Return a deterministic result after validating capabilities."""

        _ensure_supported(self.kind, self.capabilities, task)
        output = self._output if self._output is not None else f"Fake result for: {task.goal}"
        parsed = {"output": output} if task.output_schema is not None else None
        tool_calls = (
            ToolCallAudit(tool_name="fake", arguments={"goal": task.goal}, result_preview=output),
        )
        return AgentResult(
            output=output,
            parsed_output=parsed,
            tool_calls=tool_calls,
            session_id=task.session_id or task.task_id,
            rounds=1,
            metadata={"task_id": task.task_id, **self._metadata},
        )

    async def cancel(self, task_id: str) -> None:
        """Record cancellation requests for assertions."""

        self.cancelled_task_ids.add(task_id)


def _ensure_supported(
    kind: AgentRuntimeKind,
    capabilities: AgentCapabilities,
    task: AgentTask,
) -> None:
    if task.mcp_servers and not capabilities.mcp_support:
        raise UnsupportedTaskInputError(kind, "mcp_servers", "runtime does not support MCP")
    if task.working_directory is not None and not capabilities.working_directory:
        raise UnsupportedTaskInputError(
            kind,
            "working_directory",
            "runtime does not support per-task working directories",
        )
    if (task.session_id or task.resume_from) and not capabilities.session_resume:
        raise UnsupportedTaskInputError(
            kind,
            "session_id",
            "runtime does not support session resume",
        )
    if task.output_schema is not None and not capabilities.structured_output:
        raise UnsupportedTaskInputError(
            kind,
            "output_schema",
            "runtime does not support structured output",
        )
