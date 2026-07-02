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


class ExplodingEventSink:
    """Event sink whose emit always raises, to prove safe_emit never aborts a run."""

    def __init__(self) -> None:
        self.calls = 0

    async def emit(self, event: object) -> None:
        self.calls += 1
        raise RuntimeError("sink is down")


@pytest.mark.asyncio
async def test_safe_emit_failures_do_not_abort_run() -> None:
    sink = ExplodingEventSink()
    runtime = FakeAgentRuntime(output="hello")

    # A sink that raises on every event must not propagate or fail the run.
    result = await runtime.run(AgentTask(goal="resilient", event_sink=sink))

    assert result.output == "hello"
    assert sink.calls > 0


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
            metadata={
                "api_key": "secret",
                "access_token": "abc",
                "github_token": "ghp",
                "input_tokens": 123,
                "max_tokens": 456,
                "nested": {"authorization": "token"},
            },
        ),
        "fake",
    )

    attrs = event["attributes"]
    metadata = attrs["metadata"]
    assert metadata["api_key"] == "[redacted]"
    assert metadata["nested"]["authorization"] == "[redacted]"
    # "token" as a full underscore segment redacts.
    assert metadata["access_token"] == "[redacted]"
    assert metadata["github_token"] == "[redacted]"
    # "token" only as a substring (not its own segment) must NOT redact.
    assert metadata["input_tokens"] == 123
    assert metadata["max_tokens"] == 456
    assert attrs["task_goal"].endswith("...[truncated]")


def test_events_redact_camelcase_secret_keys() -> None:
    event = task_started_event(
        AgentTask(
            goal="x",
            metadata={
                "accessToken": "a",
                "refreshToken": "b",
                "authToken": "c",
                "apiKey": "d",
                # camelCase "token" plural is a count, not a secret -> keep it.
                "inputTokens": 7,
                # deliberately-emitted non-secret label must survive.
                "auth_source": "anthropic-api-key",
                "session_id": "sess-1",
            },
        ),
        "fake",
    )

    metadata = event["attributes"]["metadata"]
    assert metadata["accessToken"] == "[redacted]"
    assert metadata["refreshToken"] == "[redacted]"
    assert metadata["authToken"] == "[redacted]"
    assert metadata["apiKey"] == "[redacted]"
    assert metadata["inputTokens"] == 7
    assert metadata["auth_source"] == "anthropic-api-key"
    assert metadata["session_id"] == "sess-1"


def test_events_redact_smashed_case_token_keys() -> None:
    event = task_started_event(
        AgentTask(
            goal="x",
            metadata={
                # No separator and no camelCase boundary: these never split into a
                # bare "token" segment, so they slipped through before the suffix rule.
                "accesstoken": "a",
                "ACCESSTOKEN": "b",
                "sessiontoken": "c",
                "token": "d",
                # Smashed plural counts stay visible ("...tokens", not "token").
                "inputtokens": 11,
                "totaltokens": 22,
            },
        ),
        "fake",
    )

    metadata = event["attributes"]["metadata"]
    assert metadata["accesstoken"] == "[redacted]"
    assert metadata["ACCESSTOKEN"] == "[redacted]"
    assert metadata["sessiontoken"] == "[redacted]"
    assert metadata["token"] == "[redacted]"
    assert metadata["inputtokens"] == 11
    assert metadata["totaltokens"] == 22


def test_event_construction_survives_cyclic_metadata() -> None:
    cyclic: dict[str, object] = {"self": None}
    cyclic["self"] = cyclic

    # A cycle in user metadata must not raise (RecursionError) out of the builder,
    # which runs before safe_emit's guard and would otherwise abort run().
    event = task_started_event(AgentTask(goal="x", metadata={"loop": cyclic}), "fake")

    assert event["attributes"]["metadata"]["loop"]["self"] == "[truncated: cycle]"


def test_event_construction_bounds_deep_metadata() -> None:
    node: dict[str, object] = {}
    root = node
    for _ in range(50):
        child: dict[str, object] = {}
        node["child"] = child
        node = child

    event = task_started_event(AgentTask(goal="x", metadata={"deep": root}), "fake")

    # Somewhere within the depth bound the recursion is cut off cleanly.
    assert "truncated: max depth" in repr(event["attributes"]["metadata"])


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
