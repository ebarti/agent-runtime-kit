from __future__ import annotations

import pytest

from agent_runtime_kit import AgentTask, FakeAgentRuntime
from agent_runtime_kit._types import ToolCallAudit
from agent_runtime_kit.events import task_started_event
from agent_runtime_kit.testing import (
    FakeSDKHarness,
    FakeSDKRuntime,
    FakeSDKScenario,
    RecordingEventSink,
)


@pytest.mark.asyncio
async def test_fake_runtime_emits_normalized_events() -> None:
    sink = RecordingEventSink()
    runtime = FakeAgentRuntime(output="hello")

    await runtime.run(AgentTask(goal="emit events", event_sink=sink))

    assert [event["name"] for event in sink.events] == [
        "agent.task.started",
        "agent.output.delta",
        "agent.tool.requested",
        "agent.tool.completed",
        "agent.vendor.turn",
        "agent.task.completed",
    ]


def test_events_redact_sensitive_metadata_and_truncate_text() -> None:
    event = task_started_event(
        AgentTask(
            goal="x" * 1100,
            metadata={"api_key": "secret", "nested": {"authorization": "token"}},
        ),
        "fake",
    )

    attrs = event["attributes"]
    assert attrs["metadata"]["api_key"] == "[redacted]"
    assert attrs["metadata"]["nested"]["authorization"] == "[redacted]"
    assert attrs["task_goal"].endswith("...[truncated]")


@pytest.mark.asyncio
async def test_fake_sdk_harness_simulates_success_with_tools() -> None:
    sink = RecordingEventSink()
    harness = FakeSDKHarness(
        FakeSDKScenario(
            output="ok",
            structured_output={"ok": True},
            tool_events=(
                ToolCallAudit(tool_name="Read", arguments={"path": "README.md"}, status="ok"),
            ),
        )
    )
    runtime = FakeSDKRuntime(harness)

    result = await runtime.run(AgentTask(goal="run", event_sink=sink))

    assert result.output == "ok"
    assert result.parsed_output == {"ok": True}
    assert result.tool_calls[0].tool_name == "Read"
    assert "agent.tool.completed" in [event["name"] for event in sink.events]


@pytest.mark.asyncio
async def test_fake_sdk_harness_simulates_failure() -> None:
    runtime = FakeSDKRuntime(FakeSDKHarness(FakeSDKScenario(error="boom")))

    result = await runtime.run(AgentTask(goal="fail"))

    assert result.finish_reason == "failed"
    assert result.error == "boom"


@pytest.mark.asyncio
async def test_fake_sdk_harness_simulates_timeout() -> None:
    runtime = FakeSDKRuntime(FakeSDKHarness(FakeSDKScenario(timeout=True)))

    with pytest.raises(TimeoutError):
        await runtime.run(AgentTask(goal="timeout"))
