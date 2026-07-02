from __future__ import annotations

import asyncio
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
    RuntimeAvailability,
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
    instances: list[FakeAgent] = []
    enter_errors: list[BaseException | None] = []
    chat_errors: list[BaseException | None] = []

    def __init__(self, config: FakeConfig) -> None:
        FakeAgent.last_config = config
        self.conversation_id = "ag-session"
        self.closed = False
        self.prompts: list[str] = []
        FakeAgent.instances.append(self)

    async def __aenter__(self) -> FakeAgent:
        if FakeAgent.enter_errors:
            error = FakeAgent.enter_errors.pop(0)
            if error is not None:
                raise error
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def chat(self, prompt: str) -> FakeResponse:
        self.prompts.append(prompt)
        if FakeAgent.chat_errors:
            error = FakeAgent.chat_errors.pop(0)
            if error is not None:
                raise error
        return FakeResponse(prompt, FakeAgent.chunks_factory or _chunks)


async def _chunks(prompt: str):
    yield FakeTypes.Text(f"done: {prompt}")
    yield FakeTypes.Thought("thinking")
    yield FakeTypes.ToolCall("Read", {"path": "README.md"})
    yield FakeTypes.ToolResult("Read", "ok", {"path": "README.md"})


def make_runtime(
    *,
    data_dir: Path,
    api_key: str | None = "key",
    vertex: bool | None = None,
    project: str | None = None,
    location: str | None = None,
    reuse_process: bool = False,
) -> AntigravityAgentRuntime:
    return AntigravityAgentRuntime(
        api_key=api_key,
        vertex=vertex,
        project=project,
        location=location,
        data_dir=data_dir,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
        reuse_process=reuse_process,
    )


@pytest.fixture(autouse=True)
def _reset_agent() -> None:
    FakeAgent.last_config = None
    FakeAgent.chunks_factory = None
    FakeAgent.instances.clear()
    FakeAgent.enter_errors = []
    FakeAgent.chat_errors = []


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
    # ToolCall + its ToolResult must collapse into ONE completed audit entry,
    # not a requested/ok pair — this is the cardinality contract.
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "Read"
    assert result.tool_calls[0].status == "ok"
    assert result.usage.input_tokens == 4
    assert sink.events[-1]["name"] == "agent.task.completed"
    assert FakeAgent.last_config is not None
    assert FakeAgent.last_config.kwargs["workspaces"] == [str(tmp_path)]
    assert FakeAgent.last_config.kwargs["api_key"] == "key"


@pytest.mark.asyncio
async def test_antigravity_tolerates_config_option_drift(tmp_path: Path) -> None:
    # A config class that lost NON-security kwargs must not crash the run;
    # dropped options are recorded like the Claude/Codex adapters.
    class NarrowConfig:
        def __init__(
            self,
            *,
            model: str,
            api_key: str | None = None,
            capabilities: Any = None,
            policies: Any = None,
        ) -> None:
            self.model = model
            self.api_key = api_key
            self.kwargs = {
                "model": model,
                "api_key": api_key,
                "capabilities": capabilities,
                "policies": policies,
            }

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=NarrowConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "done"
    assert "save_dir" in result.metadata["dropped_options"]


@pytest.mark.asyncio
async def test_antigravity_fails_closed_when_sdk_drops_tool_posture(tmp_path: Path) -> None:
    # capabilities/policies ARE the tool posture: a config class that no longer
    # accepts them must fail the run, not silently grant the SDK default access.
    class NoPostureConfig:
        def __init__(self, *, model: str, api_key: str | None = None) -> None:
            self.model = model
            self.api_key = api_key
            self.kwargs = {"model": model, "api_key": api_key}

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=NoPostureConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(AgentTask(goal="x"))

    assert exc_info.value.field == "permissions"


@pytest.mark.asyncio
async def test_antigravity_counts_requested_tool_call_without_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def only_call(prompt: str):
        yield FakeTypes.Text("working")
        yield FakeTypes.ToolCall("Search", {"q": "x"})

    monkeypatch.setattr(FakeAgent, "chunks_factory", staticmethod(only_call))
    runtime = make_runtime(data_dir=tmp_path)

    result = await runtime.run(AgentTask(goal="x"))

    # A tool call with no ToolResult chunk is still counted (cardinality parity).
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].tool_name == "Search"
    assert result.tool_calls[0].status == "requested"


@pytest.mark.asyncio
async def test_antigravity_tool_result_without_args_keeps_requested_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def argless_result(prompt: str):
        yield FakeTypes.ToolCall("Read", {"path": "README.md"})
        # ToolResult chunks may omit args entirely.
        yield FakeTypes.ToolResult("Read", "contents")

    monkeypatch.setattr(FakeAgent, "chunks_factory", staticmethod(argless_result))
    runtime = make_runtime(data_dir=tmp_path)

    result = await runtime.run(AgentTask(goal="x"))

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].status == "ok"
    # The merged audit keeps the request-time arguments instead of degrading
    # to an empty mapping when the result chunk carries none.
    assert result.tool_calls[0].arguments == {"path": "README.md"}


@pytest.mark.asyncio
async def test_antigravity_session_id_falls_back_to_task(tmp_path: Path) -> None:
    class NoIdResponse:
        def __init__(self, prompt: str) -> None:
            self.chunks = _chunks(prompt)
            self.usage_metadata = FakeUsage()

        async def structured_output(self) -> None:
            return None

    class NoIdAgent:
        def __init__(self, config: FakeConfig) -> None:
            self.conversation_id = None

        async def __aenter__(self) -> NoIdAgent:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def chat(self, prompt: str) -> NoIdResponse:
            return NoIdResponse(prompt)

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=NoIdAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(AgentTask(goal="x", session_id="conv-77"))

    assert result.session_id == "conv-77"


@pytest.mark.asyncio
async def test_antigravity_max_tokens_stop_reason_fails(tmp_path: Path) -> None:
    class TruncatedResponse:
        def __init__(self, prompt: str) -> None:
            self.chunks = _chunks(prompt)
            self.usage_metadata = FakeUsage()
            self.finish_reason = "MAX_TOKENS"

        async def structured_output(self) -> None:
            return None

    class TruncatingAgent:
        def __init__(self, config: FakeConfig) -> None:
            self.conversation_id = "ag-session"

        async def __aenter__(self) -> TruncatingAgent:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def chat(self, prompt: str) -> TruncatedResponse:
            return TruncatedResponse(prompt)

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=TruncatingAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(AgentTask(goal="x"))

    # A token-limit stop is a failure, not a successful completion of partial text.
    assert result.finish_reason == "max_tokens"
    assert result.error is not None


@pytest.mark.asyncio
async def test_antigravity_unknown_stop_reason_fails_not_done(tmp_path: Path) -> None:
    # SAFETY (or any unrecognized future stop reason) must surface as a failure,
    # never as a silent successful completion of the partial text.
    class BlockedResponse:
        def __init__(self, prompt: str) -> None:
            self.chunks = _chunks(prompt)
            self.usage_metadata = FakeUsage()
            self.finish_reason = "SAFETY"

        async def structured_output(self) -> None:
            return None

    class BlockedAgent:
        def __init__(self, config: FakeConfig) -> None:
            self.conversation_id = "ag-session"

        async def __aenter__(self) -> BlockedAgent:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def chat(self, prompt: str) -> BlockedResponse:
            return BlockedResponse(prompt)

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=BlockedAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert result.error is not None
    assert "SAFETY" in result.error


@pytest.mark.asyncio
async def test_antigravity_skips_structured_output_without_schema(tmp_path: Path) -> None:
    class StrictResponse:
        def __init__(self, prompt: str) -> None:
            self.chunks = _chunks(prompt)
            self.usage_metadata = FakeUsage()

        async def structured_output(self) -> None:
            raise AssertionError("structured_output() must not be called without a schema")

    class StrictAgent:
        def __init__(self, config: FakeConfig) -> None:
            self.conversation_id = "ag-session"

        async def __aenter__(self) -> StrictAgent:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def chat(self, prompt: str) -> StrictResponse:
            return StrictResponse(prompt)

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=StrictAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "done"
    assert result.parsed_output is None


@pytest.mark.asyncio
async def test_antigravity_runtime_defaults_to_per_call_process_isolation(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(AgentTask(goal="first"))
    await runtime.run(AgentTask(goal="second"))

    assert len(FakeAgent.instances) == 2
    assert [instance.closed for instance in FakeAgent.instances] == [True, True]


@pytest.mark.asyncio
async def test_antigravity_runtime_can_reuse_explicit_conversation_until_closed(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(data_dir=tmp_path, reuse_process=True)

    first = await runtime.run(AgentTask(goal="first", session_id="ag-conversation"))
    second = await runtime.run(AgentTask(goal="second", session_id="ag-conversation"))

    assert len(FakeAgent.instances) == 1
    assert FakeAgent.instances[0].closed is False
    assert FakeAgent.instances[0].prompts == ["first", "second"]
    assert first.metadata["sdk_process_reuse_enabled"] is True
    assert first.metadata["sdk_process_reused"] is False
    assert first.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reused"] is True
    assert second.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reuse_count"] == 1

    await runtime.aclose()

    assert FakeAgent.instances[0].closed is True


@pytest.mark.asyncio
async def test_antigravity_runtime_keeps_no_session_tasks_isolated(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(data_dir=tmp_path, reuse_process=True)

    first = await runtime.run(AgentTask(goal="first", task_id="task-first"))
    second = await runtime.run(AgentTask(goal="second", task_id="task-second"))

    assert first.metadata["sdk_process_reused"] is False
    assert first.metadata["sdk_process_reuse_scope"] == "task-isolated"
    assert second.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_start_count"] == 2
    assert second.metadata["sdk_process_reuse_scope"] == "task-isolated"
    assert len(FakeAgent.instances) == 2
    assert FakeAgent.instances[0].closed is True
    assert FakeAgent.instances[1].closed is False

    await runtime.aclose()

    assert FakeAgent.instances[1].closed is True


@pytest.mark.asyncio
async def test_antigravity_runtime_evicts_reused_process_after_sdk_exception(
    tmp_path: Path,
) -> None:
    FakeAgent.chat_errors = [RuntimeError("boom"), None]
    runtime = make_runtime(data_dir=tmp_path, reuse_process=True)

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.run(AgentTask(goal="fail", session_id="ag-conversation"))

    recovered = await runtime.run(AgentTask(goal="recover", session_id="ag-conversation"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 2
    assert len(FakeAgent.instances) == 2
    assert FakeAgent.instances[0].closed is True
    assert FakeAgent.instances[1].closed is False

    await runtime.aclose()

    assert FakeAgent.instances[1].closed is True


@pytest.mark.asyncio
async def test_antigravity_runtime_evicts_reused_process_after_cancellation(
    tmp_path: Path,
) -> None:
    # CancelledError is a BaseException; the reuse cleanup must still evict the
    # interrupted agent so the next run() does not inherit a poisoned process.
    FakeAgent.chat_errors = [asyncio.CancelledError(), None]
    runtime = make_runtime(data_dir=tmp_path, reuse_process=True)

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(AgentTask(goal="cancelled", session_id="ag-conversation"))

    assert FakeAgent.instances[0].closed is True

    recovered = await runtime.run(AgentTask(goal="recover", session_id="ag-conversation"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert len(FakeAgent.instances) == 2

    await runtime.aclose()


@pytest.mark.asyncio
async def test_antigravity_runtime_closes_context_after_enter_failure(
    tmp_path: Path,
) -> None:
    FakeAgent.enter_errors = [RuntimeError("enter failed"), None]
    runtime = make_runtime(data_dir=tmp_path, reuse_process=True)

    with pytest.raises(RuntimeError, match="enter failed"):
        await runtime.run(AgentTask(goal="fail", session_id="ag-conversation"))

    recovered = await runtime.run(AgentTask(goal="recover", session_id="ag-conversation"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 1
    assert len(FakeAgent.instances) == 2
    assert FakeAgent.instances[0].closed is True
    assert FakeAgent.instances[1].closed is False

    await runtime.aclose()

    assert FakeAgent.instances[1].closed is True


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
async def test_antigravity_permissive_disallowed_tools_map_to_disabled(tmp_path: Path) -> None:
    # Only PERMISSIVE takes the SDK's disabled_tools route: its baseline is every
    # tool, so "enable everything else" expresses baseline-minus-denied exactly.
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(
                mode=PermissionMode.PERMISSIVE,
                disallowed_tools=("run_command",),
            ),
        )
    )

    config = FakeAgent.last_config
    assert config is not None
    capabilities = config.kwargs["capabilities"]
    assert capabilities.disabled_tools == ["run_command"]
    # enabled_tools and disabled_tools are mutually exclusive in the real SDK.
    assert capabilities.enabled_tools is None
    assert capabilities.enable_subagents is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", [PermissionMode.DEFAULT, PermissionMode.CAUTIOUS])
async def test_antigravity_deny_list_stays_within_nondestructive_baseline(
    tmp_path: Path, mode: PermissionMode
) -> None:
    # disabled_tools means "enable everything else": under DEFAULT/CAUTIOUS that
    # would re-enable run_command (and every other destructive tool) past the
    # nondestructive baseline just by denying one unrelated tool. The deny-list
    # must stay subtractive: nondestructive minus denied.
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(mode=mode, disallowed_tools=("edit_file",)),
        )
    )

    capabilities = FakeAgent.last_config.kwargs["capabilities"]  # type: ignore[union-attr]
    assert capabilities.disabled_tools is None
    assert capabilities.enabled_tools == [
        FakeBuiltinTools.LIST_DIR,
        FakeBuiltinTools.VIEW_FILE,
        FakeBuiltinTools.FINISH,
    ]
    assert capabilities.enable_subagents is False


@pytest.mark.asyncio
async def test_antigravity_read_only_deny_list_cannot_reenable_write_tools(
    tmp_path: Path,
) -> None:
    # disabled_tools means "enable everything else": under READ_ONLY that would
    # leave every unnamed write tool enabled — the deny-list twin of the
    # allow-list backdoor. Both constraints must combine: read-only toolset
    # minus the denied tools, expressed as an allow-list.
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(
                filesystem=FilesystemAccess.READ_ONLY,
                disallowed_tools=("run_command",),
            ),
        )
    )

    capabilities = FakeAgent.last_config.kwargs["capabilities"]  # type: ignore[union-attr]
    assert capabilities.disabled_tools is None
    assert capabilities.enabled_tools == FakeBuiltinTools.read_only()
    assert capabilities.enable_subagents is False


@pytest.mark.asyncio
async def test_antigravity_strict_deny_list_constrains_to_read_only_minus_denied(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(
                mode=PermissionMode.STRICT,
                disallowed_tools=("view_file",),
            ),
        )
    )

    config = FakeAgent.last_config
    assert config is not None
    capabilities = config.kwargs["capabilities"]
    assert capabilities.disabled_tools is None
    assert capabilities.enabled_tools == [
        FakeBuiltinTools.LIST_DIR,
        FakeBuiltinTools.FINISH,
    ]
    # STRICT still carries no allow-all policy.
    assert config.kwargs["policies"] == []


@pytest.mark.asyncio
async def test_antigravity_allowed_subagent_enables_subagents_outside_permissive(
    tmp_path: Path,
) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(
                mode=PermissionMode.DEFAULT,
                allowed_tools=("start_subagent", "view_file"),
            ),
        )
    )

    config = FakeAgent.last_config
    assert config is not None
    # An explicitly allow-listed start_subagent is honored even outside PERMISSIVE.
    assert config.kwargs["capabilities"].enable_subagents is True


@pytest.mark.asyncio
async def test_antigravity_read_only_rejects_write_allowed_tool(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(
                goal="task",
                permissions=PermissionProfile(
                    filesystem=FilesystemAccess.READ_ONLY,
                    allowed_tools=("run_command",),
                ),
            )
        )


@pytest.mark.asyncio
async def test_antigravity_strict_rejects_write_allowed_tool(tmp_path: Path) -> None:
    # STRICT is a read-only posture everywhere else in the adapter (default tools,
    # deny-lists); an explicit allow-list must not be a backdoor to write tools
    # under it, even with a writable filesystem setting.
    runtime = make_runtime(data_dir=tmp_path)

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(
            AgentTask(
                goal="task",
                permissions=PermissionProfile(
                    mode=PermissionMode.STRICT,
                    filesystem=FilesystemAccess.WORKSPACE_WRITE,
                    allowed_tools=("run_command",),
                ),
            )
        )

    assert exc_info.value.field == "permissions.allowed_tools"


@pytest.mark.asyncio
async def test_antigravity_strict_honors_read_only_allow_list(tmp_path: Path) -> None:
    # The STRICT guard must reject write tools without breaking legitimate
    # read-only allow-lists.
    runtime = make_runtime(data_dir=tmp_path)

    await runtime.run(
        AgentTask(
            goal="task",
            permissions=PermissionProfile(
                mode=PermissionMode.STRICT, allowed_tools=("view_file",)
            ),
        )
    )

    config = FakeAgent.last_config
    assert config is not None
    assert config.kwargs["capabilities"].enabled_tools == [FakeBuiltinTools.VIEW_FILE]
    assert config.kwargs["policies"] == []


@pytest.mark.asyncio
async def test_antigravity_read_only_allowlist_fails_closed_without_readonly_toolset(
    tmp_path: Path,
) -> None:
    # If a future SDK drops the read-only toolset helper, the READ_ONLY +
    # allowed_tools check cannot be verified — so it must refuse, not silently
    # skip verification and pass write tools through.
    class DriftedBuiltinTools(str, enum.Enum):
        VIEW_FILE = "view_file"
        RUN_COMMAND = "run_command"
        # No read_only()/nondestructive()/all_tools() helpers.

    class DriftedTypes(FakeTypes):
        BuiltinTools = DriftedBuiltinTools

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=DriftedTypes,
        policy_module=FakePolicy,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(
            AgentTask(
                goal="task",
                permissions=PermissionProfile(
                    filesystem=FilesystemAccess.READ_ONLY,
                    allowed_tools=("view_file",),
                ),
            )
        )

    assert exc_info.value.field == "permissions.allowed_tools"


@pytest.mark.asyncio
async def test_antigravity_deny_list_fails_closed_without_nondestructive_toolset(
    tmp_path: Path,
) -> None:
    # If a future SDK drops the nondestructive helper, the DEFAULT/CAUTIOUS
    # deny-list baseline cannot be resolved — refuse rather than fall back to
    # the SDK's "enable everything else" route.
    class DriftedBuiltinTools(str, enum.Enum):
        VIEW_FILE = "view_file"
        RUN_COMMAND = "run_command"
        # No read_only()/nondestructive()/all_tools() helpers.

    class DriftedTypes(FakeTypes):
        BuiltinTools = DriftedBuiltinTools

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=DriftedTypes,
        policy_module=FakePolicy,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(
            AgentTask(
                goal="task",
                permissions=PermissionProfile(disallowed_tools=("view_file",)),
            )
        )

    assert exc_info.value.field == "permissions.disallowed_tools"


@pytest.mark.asyncio
async def test_antigravity_explicit_vertex_beats_ambient_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-should-not-win")
    runtime = AntigravityAgentRuntime(
        vertex=True,
        project="proj",
        location="us-central1",
        data_dir=tmp_path,
        agent_cls=FakeAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
    )

    await runtime.run(AgentTask(goal="task"))

    config = FakeAgent.last_config
    assert config is not None
    # Explicit vertex config must win over an ambient env API key.
    assert config.kwargs["vertex"] is True
    assert config.kwargs["project"] == "proj"
    assert config.kwargs["api_key"] is None


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
async def test_antigravity_runs_with_vertex_application_default_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._google_adc_project",
        lambda: "adc-project",
    )
    runtime = make_runtime(data_dir=tmp_path, api_key=None)

    result = await runtime.run(AgentTask(goal="task"))

    assert result.output == "done: task"
    assert FakeAgent.last_config is not None
    assert FakeAgent.last_config.kwargs["api_key"] is None
    assert FakeAgent.last_config.kwargs["vertex"] is True
    assert FakeAgent.last_config.kwargs["project"] == "adc-project"
    assert FakeAgent.last_config.kwargs["location"] == "global"


@pytest.mark.asyncio
async def test_antigravity_missing_credentials_raises_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_runtime_kit._errors import AgentRuntimeUnavailableError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    monkeypatch.setattr("agent_runtime_kit.adapters.antigravity._google_adc_project", lambda: None)
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


def test_antigravity_runtime_dir_survives_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import os

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError("not owner")

    monkeypatch.setattr(os, "chmod", boom)
    runtime = make_runtime(data_dir=tmp_path)

    # A chmod we cannot perform (e.g. a shared, non-owned data dir) must not crash.
    with caplog.at_level(logging.WARNING):
        path = runtime._runtime_dir("antigravity-sessions")

    assert path.exists()
    assert "could not enforce" in caplog.text


@pytest.mark.asyncio
async def test_antigravity_logs_when_close_fails_after_enter_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    class ExplodingAgent(FakeAgent):
        async def __aenter__(self) -> FakeAgent:
            raise RuntimeError("enter failed")

        async def __aexit__(self, *args: object) -> None:
            raise RuntimeError("close failed too")

    runtime = AntigravityAgentRuntime(
        api_key="key",
        data_dir=tmp_path,
        agent_cls=ExplodingAgent,
        config_cls=FakeConfig,
        types_module=FakeTypes,
        policy_module=FakePolicy,
        reuse_process=True,
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="enter failed"):
            await runtime.run(AgentTask(goal="x", session_id="conv"))

    assert "after startup failure" in caplog.text


def test_antigravity_availability_uses_injected_sdk(tmp_path: Path) -> None:
    runtime = make_runtime(data_dir=tmp_path)

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK
    assert diagnostic.available is True


def test_antigravity_availability_accepts_application_default_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity.package_availability",
        lambda *args, **kwargs: RuntimeAvailability.ok(
            AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
            package="google-antigravity",
            version="0.1.2",
        ),
    )
    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity._google_adc_project",
        lambda: "adc-project",
    )
    runtime = AntigravityAgentRuntime(api_key=None)

    diagnostic = runtime.availability()

    assert diagnostic.available is True
    assert diagnostic.metadata["auth_source"] == "application-default-credentials"


def test_antigravity_availability_rejects_missing_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCLOUD_PROJECT", raising=False)
    monkeypatch.setattr(
        "agent_runtime_kit.adapters.antigravity.package_availability",
        lambda *args, **kwargs: RuntimeAvailability.ok(
            AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
            package="google-antigravity",
            version="0.1.2",
        ),
    )
    monkeypatch.setattr("agent_runtime_kit.adapters.antigravity._google_adc_project", lambda: None)
    runtime = AntigravityAgentRuntime(api_key=None)

    diagnostic = runtime.availability()

    assert diagnostic.available is False
    assert diagnostic.reason.value == "missing-credentials"
