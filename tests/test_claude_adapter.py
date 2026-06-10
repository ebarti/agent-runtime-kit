from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from agent_runtime_kit import AgentRuntimeKind, AgentTask, PermissionMode, PermissionProfile
from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit.adapters import ClaudeAgentRuntime
from agent_runtime_kit.testing import RecordingEventSink


@dataclass
class FakeClaudeOptions:
    model: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    permission_mode: str | None = None
    system_prompt: str | None = None
    output_format: dict[str, Any] | None = None


async def fake_query(*, prompt: str, options: FakeClaudeOptions):
    yield {
        "type": "AssistantMessage",
        "content": [
            {"type": "text", "text": f"answer: {prompt}"},
            {"type": "tool_use", "name": "Read", "input": {"path": "README.md"}},
        ],
        "usage": {"input_tokens": 3, "output_tokens": 5},
        "session_id": "claude-session",
    }
    yield {
        "type": "ResultMessage",
        "total_cost_usd": 0.01,
        "num_turns": 1,
        "session_id": "claude-session",
    }


@pytest.mark.asyncio
async def test_claude_runtime_runs_with_injected_sdk() -> None:
    sink = RecordingEventSink()
    runtime = ClaudeAgentRuntime(query_func=fake_query, options_cls=FakeClaudeOptions)

    result = await runtime.run(
        AgentTask(
            goal="hello",
            system="system",
            permissions=PermissionProfile(mode=PermissionMode.STRICT, allowed_tools=("Read",)),
            event_sink=sink,
        )
    )

    assert result.output == "answer: hello"
    assert result.session_id == "claude-session"
    assert result.tool_calls[0].tool_name == "Read"
    assert result.cost_usd == 0.01
    assert sink.events[-1]["name"] == "agent.task.completed"


@pytest.mark.asyncio
async def test_claude_runtime_rejects_unsupported_model() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=fake_query,
        options_cls=FakeClaudeOptions,
        supported_models=("claude-sonnet-4-6",),
    )

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="x", metadata={"model": "unsupported"}))


def test_claude_availability_uses_injected_sdk() -> None:
    runtime = ClaudeAgentRuntime(query_func=fake_query, options_cls=FakeClaudeOptions)

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.CLAUDE_AGENT_SDK
    assert diagnostic.available is True
