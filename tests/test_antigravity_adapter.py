from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agent_runtime_kit import (
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    McpServerConfig,
    PermissionMode,
    PermissionProfile,
)
from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit.adapters import AntigravityAgentRuntime
from agent_runtime_kit.testing import RecordingEventSink


class FakeBuiltinTools(str, enum.Enum):
    """Mirror the real ``BuiltinTools`` enum values so validation behaves the same."""

    LIST_DIR = "list_directory"
    VIEW_FILE = "view_file"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    START_SUBAGENT = "start_subagent"
    FINISH = "finish"

    @staticmethod
    def read_only() -> list[FakeBuiltinTools]:
        return [FakeBuiltinTools.LIST_DIR, FakeBuiltinTools.VIEW_FILE, FakeBuiltinTools.FINISH]

    @staticmethod
    def nondestructive() -> list[FakeBuiltinTools]:
        return [
            FakeBuiltinTools.LIST_DIR,
            FakeBuiltinTools.VIEW_FILE,
            FakeBuiltinTools.EDIT_FILE,
            FakeBuiltinTools.FINISH,
        ]

    @staticmethod
    def all_tools() -> list[FakeBuiltinTools]:
        return list(FakeBuiltinTools)


class FakeTypes:
    BuiltinTools = FakeBuiltinTools

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
        error: str | None = None
        exception: Exception | None = None

    @dataclass
    class CapabilitiesConfig:
        enabled_tools: list[Any] | None = None
        disabled_tools: list[Any] | None = None
        enable_subagents: bool = False

    @dataclass
    class McpStdioServer:
        name: str
        command: str
        args: list[str] = field(default_factory=list)


class FakePolicy:
    @staticmethod
    def allow_all() -> str:
        return "allow_all"


class FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeUsage:
    prompt_token_count = 5
    candidates_token_count = 7
    thoughts_token_count = 2
    cached_content_token_count = 1
    total_token_count = 14


class FakeResponse:
    def __init__(self, prompt: str, chunks_factory: Any) -> None:
        self.chunks = chunks_factory(prompt)
        self.usage_metadata = FakeUsage()

    async def structured_output(self) -> dict[str, bool]:
        return {"ok": True}


class FakeAgent:
    last_config: FakeConfig | None = None
    chunks_factory: Any = None

    def __init__(self, config: FakeConfig) -> None:
        FakeAgent.last_config = config
        self.conversation_id = "ag-session"

    async def __aenter__(self) -> FakeAgent:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def chat(self, prompt: str) -> FakeResponse:
        return FakeResponse(prompt, FakeAgent.chunks_factory or _chunks)


async def _chunks(prompt: str):
    yield FakeTypes.Text(f"done: {prompt}")
    yield FakeTypes.Thought("thinking")
    yield FakeTypes.ToolCall("Read", {"path": "README.md"})
    yield FakeTypes.ToolResult("Read", "ok", {"path": "README.md"})


def make_runtime(*, data_dir: Path, api_key: str = "key") -> AntigravityAgentRuntime:
    return AntigravityAgentRuntime(
        api_key=api_key,
        data_dir=data_dir,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )


@pytest.fixture(autouse=True)
def _reset_agent() -> None:
    FakeAgent.last_config = None
    FakeAgent.chunks_factory = None


@pytest.mark.asyncio
async def test_antigravity_runtime_runs_with_injected_sdk(tmp_path: Path) -> None:
    sink = RecordingEventSink()
    runtime = make_runtime(data_dir=tmp_path)

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
async def test_antigravity_mcp_server_gets_name(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            mcp_servers=(McpServerConfig(name="fs", command="mcp", args=("--root",)),),
        )
    )

    assert FakeAgent.last_config is not None
    servers = FakeAgent.last_config.kwargs["mcp_servers"]
    assert servers[0].name == "fs"
    assert servers[0].command == "mcp"


@pytest.mark.asyncio
async def test_antigravity_default_mode_uses_nondestructive(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(AgentTask(goal="task"))

    capabilities = FakeAgent.last_config.kwargs["capabilities"]  # type: ignore[union-attr]
    assert capabilities.enabled_tools == FakeBuiltinTools.nondestructive()


@pytest.mark.asyncio
async def test_antigravity_permissive_uses_all_tools(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(goal="task", permissions=PermissionProfile(mode=PermissionMode.PERMISSIVE))
    )

    config = FakeAgent.last_config
    assert config is not None
    assert config.kwargs["capabilities"].enabled_tools == FakeBuiltinTools.all_tools()
    assert config.kwargs["capabilities"].enable_subagents is True
    assert config.kwargs["policies"] == ["allow_all"]


@pytest.mark.asyncio
async def test_antigravity_strict_uses_read_only_and_no_policies(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(goal="task", permissions=PermissionProfile(mode=PermissionMode.STRICT))
    )

    config = FakeAgent.last_config
    assert config is not None
    assert config.kwargs["capabilities"].enabled_tools == FakeBuiltinTools.read_only()
    assert config.kwargs["policies"] == []


@pytest.mark.asyncio
async def test_antigravity_disallowed_tools_map_to_disabled(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(disallowed_tools=("run_command",)),
        )
    )

    config = FakeAgent.last_config
    assert config is not None
    capabilities = config.kwargs["capabilities"]
    assert capabilities.disabled_tools == ["run_command"]
    # enabled_tools and disabled_tools are mutually exclusive in the real SDK.
    assert capabilities.enabled_tools is None


@pytest.mark.asyncio
async def test_antigravity_rejects_allow_and_deny_list_together(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(
                goal="task",
                permissions=PermissionProfile(
                    allowed_tools=("view_file",), disallowed_tools=("run_command",)
                ),
            )
        )


@pytest.mark.asyncio
async def test_antigravity_invalid_allowed_tool_raises(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="task", permissions=PermissionProfile(allowed_tools=("Read",)))
        )


@pytest.mark.asyncio
async def test_antigravity_tool_result_error_status(tmp_path: Path) -> None:
    async def error_chunks(prompt: str):
        yield FakeTypes.Text("partial")
        yield FakeTypes.ToolResult("Edit", "", {"p": "x"}, error="disk full")

    FakeAgent.chunks_factory = error_chunks
    runtime = make_runtime(data_dir=tmp_path)

    result = await runtime.run(AgentTask(goal="task"))

    assert result.tool_calls[0].status == "error"


@pytest.mark.asyncio
async def test_antigravity_rejects_budget(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="task", budget_usd=1.0))


@pytest.mark.asyncio
async def test_antigravity_rejects_network(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="task", permissions=PermissionProfile(network=True))
        )


@pytest.mark.asyncio
async def test_antigravity_rejects_mcp_env(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(
                goal="task",
                mcp_servers=(McpServerConfig(name="fs", command="mcp", env={"X": "1"}),),
            )
        )


@pytest.mark.asyncio
async def test_antigravity_missing_api_key_raises_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_runtime_kit._errors import AgentRuntimeUnavailableError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    runtime = AntigravityAgentRuntime(
        api_key=None,
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    with pytest.raises(AgentRuntimeUnavailableError):
        await runtime.run(AgentTask(goal="task"))


def test_antigravity_data_dir_is_private(tmp_path: Path) -> None:
    import os
    import stat

    runtime = make_runtime(data_dir=tmp_path)

    path = runtime._runtime_dir("antigravity-sessions")

    assert path.parent == tmp_path
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o700


def test_antigravity_availability_uses_injected_sdk(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK
    assert diagnostic.available is True
