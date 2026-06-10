from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_runtime_kit import (
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    McpServerConfig,
    PermissionProfile,
)
from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit.adapters import AntigravityAgentRuntime
from agent_runtime_kit.testing import RecordingEventSink


class FakeTypes:
    @dataclass
    class Text:
        text: str

    @dataclass
    class Thought:
        text: str

    @dataclass
    class ToolCall:
        name: str
        args: dict[str, Any]

    @dataclass
    class ToolResult:
        name: str
        result: str
        args: dict[str, Any] | None = None

    @dataclass
    class CapabilitiesConfig:
        enabled_tools: list[Any]
        enable_subagents: bool = False

    @dataclass
    class McpStdioServer:
        command: str
        args: list[str]

    class BuiltinTools:
        START_SUBAGENT = "start_subagent"

        @staticmethod
        def read_only() -> list[str]:
            return ["read"]

        @staticmethod
        def nondestructive() -> list[str]:
            return ["read", "search"]

        @staticmethod
        def all_tools() -> list[str]:
            return ["read", "write", "start_subagent"]


class FakePolicy:
    @staticmethod
    def allow_all() -> str:
        return "allow_all"


class FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeAgent:
    last_config: FakeConfig | None = None

    def __init__(self, config: FakeConfig) -> None:
        FakeAgent.last_config = config
        self.conversation_id = "ag-session"

    async def __aenter__(self) -> FakeAgent:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def chat(self, prompt: str) -> FakeResponse:
        return FakeResponse(prompt)


class FakeUsage:
    prompt_token_count = 5
    candidates_token_count = 7
    thoughts_token_count = 2
    cached_content_token_count = 1
    total_token_count = 14


class FakeResponse:
    def __init__(self, prompt: str) -> None:
        self.chunks = _chunks(prompt)
        self.usage_metadata = FakeUsage()

    async def structured_output(self) -> dict[str, bool]:
        return {"ok": True}


async def _chunks(prompt: str):
    yield FakeTypes.Text(f"done: {prompt}")
    yield FakeTypes.Thought("thinking")
    yield FakeTypes.ToolCall("Read", {"path": "README.md"})
    yield FakeTypes.ToolResult("Read", "ok", {"path": "README.md"})


@pytest.mark.asyncio
async def test_antigravity_runtime_runs_with_injected_sdk(tmp_path: Path) -> None:
    sink = RecordingEventSink()
    runtime = AntigravityAgentRuntime(
        api_key="key",
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(
        AgentTask(
            goal="task",
            working_directory=tmp_path,
            event_sink=sink,
            output_schema={"type": "object"},
            permissions=PermissionProfile(filesystem=FilesystemAccess.READ_ONLY),
            mcp_servers=(McpServerConfig(name="fs", command="mcp", args=("--root", ".")),),
        )
    )

    assert result.output == "done: task"
    assert result.parsed_output == {"ok": True}
    assert result.session_id == "ag-session"
    assert result.tool_calls[0].tool_name == "Read"
    assert result.usage.input_tokens == 4
    assert sink.events[-1]["name"] == "agent.task.completed"
    assert FakeAgent.last_config is not None
    assert FakeAgent.last_config.kwargs["workspaces"] == [str(tmp_path)]


@pytest.mark.asyncio
async def test_antigravity_rejects_mcp_env() -> None:
    runtime = AntigravityAgentRuntime(
        api_key="key",
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(
                goal="task",
                mcp_servers=(McpServerConfig(name="fs", command="mcp", env={"X": "1"}),),
            )
        )


def test_antigravity_availability_uses_injected_sdk() -> None:
    runtime = AntigravityAgentRuntime(
        api_key="key",
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK
    assert diagnostic.available is True
