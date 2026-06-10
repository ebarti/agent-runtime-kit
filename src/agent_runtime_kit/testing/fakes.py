"""Fake SDK fixtures for runtime adapter contract tests."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from agent_runtime_kit._errors import AgentRuntimeUnavailableError, UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    RuntimeAvailability,
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


@dataclass(frozen=True)
class FakeSDKScenario:
    """Scripted fake SDK behavior used by adapter tests."""

    output: str = "fake sdk output"
    error: str | None = None
    missing_dependency: bool = False
    unsupported_fields: tuple[str, ...] = ()
    timeout: bool = False
    session_id: str | None = "fake-session"
    structured_output: Any | None = None
    tool_events: tuple[ToolCallAudit, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


class FakeSDKHarness:
    """Credential-free fake SDK surface with scripted outcomes."""

    def __init__(
        self,
        scenario: FakeSDKScenario | None = None,
        *,
        kind: AgentRuntimeKind = AgentRuntimeKind.FAKE,
    ) -> None:
        self.scenario = scenario or FakeSDKScenario()
        self.kind = kind
        self.calls: list[AgentTask] = []

    async def invoke(self, task: AgentTask) -> AgentResult:
        """Simulate one SDK invocation."""

        self.calls.append(task)
        scenario = self.scenario
        if scenario.missing_dependency:
            raise AgentRuntimeUnavailableError(self.kind, "fake SDK package is missing")
        if scenario.timeout:
            raise TimeoutError("fake SDK timeout")
        if scenario.unsupported_fields:
            field_name = scenario.unsupported_fields[0]
            raise UnsupportedTaskInputError(self.kind, field_name, "scripted unsupported input")
        if scenario.error is not None:
            return AgentResult(
                output="",
                finish_reason="failed",
                error=scenario.error,
                session_id=scenario.session_id,
                metadata=dict(scenario.metadata),
            )
        return AgentResult(
            output=scenario.output,
            parsed_output=scenario.structured_output,
            tool_calls=scenario.tool_events,
            session_id=scenario.session_id,
            rounds=1,
            metadata=dict(scenario.metadata),
        )


class FakeSDKRuntime:
    """Runtime wrapper around ``FakeSDKHarness`` for contract tests."""

    kind = AgentRuntimeKind.FAKE
    capabilities = AgentCapabilities(
        mcp_support=True,
        working_directory=True,
        session_resume=True,
        structured_output=True,
        streaming=True,
        tool_audit=True,
        cancellation=True,
    )

    def __init__(self, harness: FakeSDKHarness | None = None) -> None:
        self.harness = harness or FakeSDKHarness()

    def availability(self) -> RuntimeAvailability:
        """Return fake availability based on the scripted scenario."""

        if self.harness.scenario.missing_dependency:
            return RuntimeAvailability.unavailable(
                self.kind,
                reason=AvailabilityReason.MISSING_PACKAGE,
                message="fake SDK package is missing",
                package="fake-sdk",
            )
        return RuntimeAvailability.ok(self.kind, package="fake-sdk", version="0.0.0")

    async def run(self, task: AgentTask) -> AgentResult:
        """Invoke the fake SDK and emit normalized events."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            result = await self.harness.invoke(task)
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise
        if result.error:
            await safe_emit(task, task_failed_event(task, self.kind, error=result.error))
            return result
        await safe_emit(task, output_delta_event(task, self.kind, text=result.output))
        for audit in result.tool_calls:
            await safe_emit(
                task,
                tool_requested_event(
                    task,
                    self.kind,
                    tool_name=audit.tool_name,
                    arguments=audit.arguments,
                ),
            )
            await safe_emit(task, tool_completed_event(task, self.kind, audit))
        await safe_emit(
            task,
            vendor_turn_event(task, self.kind, payload={"scenario": "fake"}, turn_index=1),
        )
        await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> None:
        """Fake cancellation is a no-op."""

        del task_id


class RecordingEventSink:
    """In-memory event sink for tests."""

    def __init__(self) -> None:
        self.events: list[Mapping[str, Any]] = []

    async def emit(self, event: Mapping[str, Any]) -> None:
        """Record one event."""

        self.events.append(event)
