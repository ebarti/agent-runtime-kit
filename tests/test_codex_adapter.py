from __future__ import annotations

from dataclasses import dataclass
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
from agent_runtime_kit.adapters import CodexAgentRuntime
from agent_runtime_kit.testing import RecordingEventSink


@dataclass
class FakeCodexConfig:
    cwd: str | None = None
    config_overrides: tuple[str, ...] = ()
    env: dict[str, str] | None = None


class FakeSandbox:
    read_only = "read-only"
    workspace_write = "workspace-write"
    full_access = "full-access"


class FakeApprovalMode:
    auto_review = "auto_review"
    deny_all = "deny_all"


class FakeThread:
    last_run_kwargs: dict[str, Any] | None = None

    def __init__(
        self,
        thread_id: str,
        run_result: Any | None = None,
        run_error: BaseException | None = None,
    ) -> None:
        self.id = thread_id
        self._run_result = run_result
        self._run_error = run_error

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        FakeThread.last_run_kwargs = kwargs
        if self._run_error is not None:
            raise self._run_error
        if self._run_result is not None:
            return self._run_result
        return {
            "status": "completed",
            "final_response": '{"ok": true}' if kwargs.get("output_schema") else f"done: {prompt}",
            "usage": {
                "total": {
                    "input_tokens": 4,
                    "output_tokens": 6,
                    "cached_input_tokens": 1,
                    "total_tokens": 11,
                }
            },
        }


class FakeCodex:
    last_started_kwargs: dict[str, Any] | None = None
    instances: list[FakeCodex] = []

    def __init__(
        self,
        config: FakeCodexConfig,
        run_result: Any | None = None,
        run_error: BaseException | None = None,
    ) -> None:
        self.config = config
        self._run_result = run_result
        self._run_error = run_error
        self.closed = False
        FakeCodex.instances.append(self)

    async def __aenter__(self) -> FakeCodex:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def thread_start(self, **kwargs: Any) -> FakeThread:
        FakeCodex.last_started_kwargs = kwargs
        return FakeThread("thread-new", self._run_result, self._run_error)

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
        FakeCodex.last_started_kwargs = kwargs
        return FakeThread(thread_id, self._run_result, self._run_error)


def make_runtime(
    run_result: Any | None = None,
    *,
    run_error: BaseException | None = None,
    reuse_process: bool = False,
) -> CodexAgentRuntime:
    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return FakeCodex(config, run_result, run_error)

    return CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
        reuse_process=reuse_process,
    )


@pytest.fixture(autouse=True)
def reset_fake_codex() -> None:
    FakeCodex.instances.clear()
    FakeCodex.last_started_kwargs = None
    FakeThread.last_run_kwargs = None


@pytest.mark.asyncio
async def test_codex_runtime_runs_with_injected_sdk() -> None:
    sink = RecordingEventSink()
    runtime = make_runtime()

    result = await runtime.run(
        AgentTask(
            goal="implement",
            event_sink=sink,
            output_schema={"type": "object"},
            permissions=PermissionProfile(filesystem=FilesystemAccess.READ_ONLY),
        )
    )

    assert result.output == '{"ok": true}'
    assert result.parsed_output == {"ok": True}
    assert result.usage.input_tokens == 4
    assert result.session_id == "thread-new"
    assert sink.events[-1]["name"] == "agent.task.completed"


@pytest.mark.asyncio
async def test_codex_runtime_defaults_to_per_call_process_isolation() -> None:
    runtime = make_runtime()

    await runtime.run(AgentTask(goal="first"))
    await runtime.run(AgentTask(goal="second"))

    assert len(FakeCodex.instances) == 2
    assert [instance.closed for instance in FakeCodex.instances] == [True, True]


@pytest.mark.asyncio
async def test_codex_runtime_can_reuse_process_until_closed() -> None:
    runtime = make_runtime(reuse_process=True)

    first = await runtime.run(AgentTask(goal="first"))
    second = await runtime.run(AgentTask(goal="second"))

    assert len(FakeCodex.instances) == 1
    assert FakeCodex.instances[0].closed is False
    assert first.metadata["sdk_process_reuse_enabled"] is True
    assert first.metadata["sdk_process_reused"] is False
    assert first.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reused"] is True
    assert second.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reuse_count"] == 1

    await runtime.aclose()

    assert FakeCodex.instances[0].closed is True


@pytest.mark.asyncio
async def test_codex_runtime_restarts_reused_process_on_policy_key_change(tmp_path) -> None:
    runtime = make_runtime(reuse_process=True)

    first = await runtime.run(AgentTask(goal="first"))
    second = await runtime.run(
        AgentTask(
            goal="second",
            working_directory=tmp_path,
            permissions=PermissionProfile(
                mode=PermissionMode.STRICT,
                filesystem=FilesystemAccess.READ_ONLY,
            ),
        )
    )

    assert first.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_start_count"] == 2
    assert len(FakeCodex.instances) == 2
    assert FakeCodex.instances[0].closed is True
    assert FakeCodex.instances[1].closed is False

    await runtime.aclose()

    assert FakeCodex.instances[1].closed is True


@pytest.mark.asyncio
async def test_codex_runtime_evicts_reused_process_after_sdk_exception() -> None:
    errors: list[BaseException | None] = [RuntimeError("boom"), None]

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return FakeCodex(config, run_error=errors.pop(0))

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
        reuse_process=True,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.run(AgentTask(goal="fail"))

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 2
    assert len(FakeCodex.instances) == 2
    assert FakeCodex.instances[0].closed is True
    assert FakeCodex.instances[1].closed is False

    await runtime.aclose()

    assert FakeCodex.instances[1].closed is True


@pytest.mark.asyncio
async def test_codex_thread_start_and_run_kwargs() -> None:
    runtime = make_runtime()

    await runtime.run(
        AgentTask(
            goal="g",
            system="dev instructions",
            metadata={"model": "gpt-x", "reasoning_effort": "high"},
            output_schema={"type": "object"},
        )
    )

    started = FakeCodex.last_started_kwargs
    assert started is not None
    assert started["developer_instructions"] == "dev instructions"
    assert started["model"] == "gpt-x"
    assert started["cwd"] is None
    run_kwargs = FakeThread.last_run_kwargs
    assert run_kwargs is not None
    assert run_kwargs["model"] == "gpt-x"
    assert run_kwargs["output_schema"] == {"type": "object"}
    assert run_kwargs["effort"] == "high"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PermissionMode.STRICT, "deny_all"),
        (PermissionMode.CAUTIOUS, "deny_all"),
        (PermissionMode.DEFAULT, "auto_review"),
        (PermissionMode.PERMISSIVE, "auto_review"),
    ],
)
async def test_codex_approval_mode_mapping(mode: PermissionMode, expected: str) -> None:
    runtime = make_runtime()

    await runtime.run(AgentTask(goal="x", permissions=PermissionProfile(mode=mode)))

    assert FakeThread.last_run_kwargs is not None
    assert FakeThread.last_run_kwargs["approval_mode"] == expected
    assert FakeCodex.last_started_kwargs is not None
    assert FakeCodex.last_started_kwargs["approval_mode"] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("filesystem", "expected"),
    [
        (FilesystemAccess.READ_ONLY, "read-only"),
        (FilesystemAccess.WORKSPACE_WRITE, "workspace-write"),
        (FilesystemAccess.FULL_ACCESS, "full-access"),
    ],
)
async def test_codex_sandbox_mapping(filesystem: FilesystemAccess, expected: str) -> None:
    runtime = make_runtime()

    await runtime.run(AgentTask(goal="x", permissions=PermissionProfile(filesystem=filesystem)))

    assert FakeThread.last_run_kwargs is not None
    assert FakeThread.last_run_kwargs["sandbox"] == expected


@pytest.mark.asyncio
async def test_codex_failed_status_maps_to_error() -> None:
    run_result = {
        "status": "failed",
        "final_response": "partial text that should not be treated as success",
        "error": {"message": "the sandbox blocked it"},
    }
    sink = RecordingEventSink()
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x", event_sink=sink))

    assert result.finish_reason == "failed"
    assert result.error == "the sandbox blocked it"
    assert sink.events[-1]["name"] == "agent.task.failed"


@pytest.mark.asyncio
async def test_codex_failed_status_without_error_message() -> None:
    runtime = make_runtime({"status": "failed", "final_response": "", "error": None})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert result.error == "Codex turn failed"


@pytest.mark.asyncio
async def test_codex_interrupted_status_maps_to_interrupted() -> None:
    runtime = make_runtime({"status": "interrupted", "final_response": "stopped"})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "interrupted"
    assert result.error == "Codex turn interrupted"


@pytest.mark.asyncio
async def test_codex_builds_tool_audits_from_items() -> None:
    run_result = {
        "status": "completed",
        "final_response": "done",
        "items": [
            {"type": "agentMessage", "text": "ignored"},
            {
                "type": "commandExecution",
                "command": "ls -la",
                "status": "completed",
                "aggregated_output": "file1\nfile2",
                "duration_ms": 12,
            },
            {
                "type": "mcpToolCall",
                "tool": "search",
                "server": "docs",
                "arguments": {"q": "x"},
                "status": "failed",
                "error": {"message": "nope"},
            },
            {"type": "webSearch", "query": "python"},
        ],
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    names = [(tc.tool_name, tc.status) for tc in result.tool_calls]
    assert ("command", "ok") in names
    assert ("search", "error") in names
    assert ("web_search", "ok") in names
    command_audit = next(tc for tc in result.tool_calls if tc.tool_name == "command")
    assert command_audit.arguments == {"command": "ls -la"}
    assert command_audit.duration_ms == 12


@pytest.mark.asyncio
async def test_codex_unwraps_root_model_items() -> None:
    @dataclass
    class _RootWrapper:
        root: Any

    @dataclass
    class _Command:
        type: str
        command: str
        status: str

    run_result = {
        "status": "completed",
        "final_response": "done",
        "items": [
            _RootWrapper(_Command(type="commandExecution", command="pwd", status="completed"))
        ],
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    assert result.tool_calls[0].tool_name == "command"
    assert result.tool_calls[0].arguments == {"command": "pwd"}


@pytest.mark.asyncio
async def test_codex_rejects_mcp_servers() -> None:
    runtime = make_runtime()

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="x", mcp_servers=(McpServerConfig(name="fs", command="mcp"),))
        )


@pytest.mark.asyncio
async def test_codex_rejects_allowed_tools() -> None:
    runtime = make_runtime()

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="x", permissions=PermissionProfile(allowed_tools=("Read",)))
        )


@pytest.mark.asyncio
async def test_codex_rejects_disallowed_tools() -> None:
    runtime = make_runtime()

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="x", permissions=PermissionProfile(disallowed_tools=("Bash",)))
        )


@pytest.mark.asyncio
async def test_codex_rejects_budget() -> None:
    runtime = make_runtime()

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="x", budget_usd=1.0))


@pytest.mark.asyncio
async def test_codex_rejects_network() -> None:
    runtime = make_runtime()

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="x", permissions=PermissionProfile(network=False)))


@pytest.mark.asyncio
async def test_codex_usage_fallback_excludes_cached() -> None:
    run_result = {
        "status": "completed",
        "final_response": "done",
        "usage": {"total": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 4}},
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    # Fallback total is input + output only (cached is already inside input_tokens).
    assert result.usage.total_tokens == 15


def test_codex_availability_uses_injected_sdk() -> None:
    runtime = CodexAgentRuntime(codex_cls=FakeCodex, config_cls=FakeCodexConfig)

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.CODEX_AGENT_SDK
    assert diagnostic.available is True


@pytest.mark.asyncio
async def test_codex_passes_runtime_env_to_config() -> None:
    seen: dict[str, Any] = {}

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        seen["config"] = config
        return FakeCodex(config)

    runtime = CodexAgentRuntime(
        env={"AWS_PROFILE": "agent-runtime-kit"},
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    await runtime.run(AgentTask(goal="x"))

    assert seen["config"].env == {"AWS_PROFILE": "agent-runtime-kit"}


def test_codex_availability_reports_bedrock_provider() -> None:
    runtime = CodexAgentRuntime(
        config_overrides=(
            "features.plugins=false",
            "model_provider=amazon-bedrock",
            "model=anthropic.claude-sonnet-4-6",
        ),
        env={"AWS_PROFILE": "agent-runtime-kit", "AWS_REGION": "us-east-1"},
        codex_cls=FakeCodex,
        config_cls=FakeCodexConfig,
    )

    diagnostic = runtime.availability()

    assert diagnostic.available is True
    assert diagnostic.metadata["auth_source"] == "amazon-bedrock"
    assert diagnostic.metadata["credential_chain"] == "aws-sdk"
    assert diagnostic.metadata["aws_profile_configured"] is True
