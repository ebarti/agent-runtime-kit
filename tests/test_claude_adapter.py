from __future__ import annotations

import asyncio
import logging
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
    SessionResumeState,
)
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
    cwd: Any | None = None
    mcp_servers: dict[str, Any] | None = None
    resume: str | None = None
    max_budget_usd: float | None = None
    output_format: dict[str, Any] | None = None
    setting_sources: list[str] | None = None
    env: dict[str, str] | None = None
    max_turns: int | None = None


# Records the options object passed to query so tests can assert request shape.
RECORDED: dict[str, Any] = {}


class FakeClaudeClient:
    messages: list[Any] = []
    connect_errors: list[BaseException | None] = []
    query_errors: list[BaseException | None] = []
    instances: list[FakeClaudeClient] = []

    def __init__(self, options: Any) -> None:
        self.options = options
        self.connected = False
        self.closed = False
        self.queries: list[tuple[str, str]] = []
        FakeClaudeClient.instances.append(self)

    async def connect(self) -> None:
        self.connected = True
        if FakeClaudeClient.connect_errors:
            error = FakeClaudeClient.connect_errors.pop(0)
            if error is not None:
                raise error

    async def disconnect(self) -> None:
        self.closed = True

    async def query(self, prompt: str, *, session_id: str = "default") -> None:
        self.queries.append((prompt, session_id))
        if FakeClaudeClient.query_errors:
            error = FakeClaudeClient.query_errors.pop(0)
            if error is not None:
                raise error

    async def receive_response(self):
        for message in FakeClaudeClient.messages:
            yield message


@pytest.fixture(autouse=True)
def reset_fakes() -> None:
    RECORDED.clear()
    FakeClaudeClient.messages = []
    FakeClaudeClient.connect_errors = []
    FakeClaudeClient.query_errors = []
    FakeClaudeClient.instances.clear()


def make_query(messages: list[Any]) -> Any:
    async def fake_query(*, prompt: str, options: Any):
        RECORDED["options"] = options
        RECORDED["prompt"] = prompt
        for message in messages:
            yield message

    return fake_query


def assistant(text: str, *, tools: list[dict[str, Any]] | None = None, **extra: Any) -> dict:
    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    content.extend(tools or [])
    return {"type": "AssistantMessage", "content": content, **extra}


def result_message(**extra: Any) -> dict:
    base = {"type": "ResultMessage", "num_turns": 1, "session_id": "claude-session"}
    base.update(extra)
    return base


@pytest.mark.asyncio
async def test_claude_runtime_runs_with_injected_sdk() -> None:
    sink = RecordingEventSink()
    runtime = ClaudeAgentRuntime(
        query_func=make_query(
            [
                assistant(
                    "answer: hello",
                    tools=[{"type": "tool_use", "id": "t1", "name": "Read", "input": {"p": "x"}}],
                    usage={"input_tokens": 3, "output_tokens": 5},
                    session_id="claude-session",
                ),
                result_message(total_cost_usd=0.01),
            ]
        ),
        options_cls=FakeClaudeOptions,
    )

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
async def test_claude_runtime_can_reuse_process_until_closed() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    first = await runtime.run(AgentTask(goal="first", task_id="task-first"))
    second = await runtime.run(AgentTask(goal="second", task_id="task-second"))

    assert len(FakeClaudeClient.instances) == 1
    assert FakeClaudeClient.instances[0].connected is True
    assert FakeClaudeClient.instances[0].closed is False
    assert FakeClaudeClient.instances[0].queries == [
        ("first", "task-first"),
        ("second", "task-second"),
    ]
    assert first.metadata["sdk_process_reuse_enabled"] is True
    assert first.metadata["sdk_process_reused"] is False
    assert first.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reused"] is True
    assert second.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reuse_count"] == 1

    await runtime.aclose()

    assert FakeClaudeClient.instances[0].closed is True


@pytest.mark.asyncio
async def test_claude_runtime_restarts_reused_process_on_option_change(tmp_path: Path) -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    first = await runtime.run(AgentTask(goal="first"))
    second = await runtime.run(AgentTask(goal="second", working_directory=tmp_path))

    assert first.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_start_count"] == 2
    assert len(FakeClaudeClient.instances) == 2
    assert FakeClaudeClient.instances[0].closed is True
    assert FakeClaudeClient.instances[1].closed is False

    await runtime.aclose()

    assert FakeClaudeClient.instances[1].closed is True


@pytest.mark.asyncio
async def test_claude_runtime_evicts_reused_process_after_sdk_exception() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    FakeClaudeClient.query_errors = [RuntimeError("boom"), None]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    with pytest.raises(RuntimeError, match="boom"):
        await runtime.run(AgentTask(goal="fail"))

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "ok"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 2
    assert len(FakeClaudeClient.instances) == 2
    assert FakeClaudeClient.instances[0].closed is True
    assert FakeClaudeClient.instances[1].closed is False

    await runtime.aclose()

    assert FakeClaudeClient.instances[1].closed is True


@pytest.mark.asyncio
async def test_claude_runtime_evicts_reused_process_after_cancellation() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    # CancelledError is a BaseException; the reuse cleanup must still evict the
    # interrupted client so the next run() does not inherit a poisoned process.
    FakeClaudeClient.query_errors = [asyncio.CancelledError(), None]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(AgentTask(goal="cancelled"))

    assert FakeClaudeClient.instances[0].closed is True

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "ok"
    assert recovered.metadata["sdk_process_reused"] is False
    assert len(FakeClaudeClient.instances) == 2

    await runtime.aclose()


@pytest.mark.asyncio
async def test_claude_aclose_waits_for_in_flight_run() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    # Holding the run lock simulates an in-flight run(); aclose() must block on it
    # rather than racing in and closing the shared client mid-stream.
    await runtime._client_run_lock.acquire()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(runtime.aclose(), timeout=0.05)
    finally:
        runtime._client_run_lock.release()

    # Once the run releases the lock, aclose() proceeds.
    await asyncio.wait_for(runtime.aclose(), timeout=1.0)


@pytest.mark.asyncio
async def test_claude_runtime_closes_client_after_connect_failure() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    FakeClaudeClient.connect_errors = [RuntimeError("connect failed"), None]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    with pytest.raises(RuntimeError, match="connect failed"):
        await runtime.run(AgentTask(goal="fail"))

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "ok"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 1
    assert len(FakeClaudeClient.instances) == 2
    assert FakeClaudeClient.instances[0].closed is True
    assert FakeClaudeClient.instances[1].closed is False

    await runtime.aclose()

    assert FakeClaudeClient.instances[1].closed is True


@pytest.mark.asyncio
async def test_claude_reuse_is_shared_for_no_session_tasks() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    first = await runtime.run(AgentTask(goal="a", task_id="task-a"))
    second = await runtime.run(AgentTask(goal="b", task_id="task-b"))

    # No explicit conversation -> shared process, isolated by per-query session id.
    assert first.metadata["sdk_process_reuse_scope"] == "shared"
    assert second.metadata["sdk_process_reused"] is True
    assert len(FakeClaudeClient.instances) == 1


@pytest.mark.asyncio
async def test_claude_reuse_restarts_for_different_sessions() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    await runtime.run(AgentTask(goal="a", session_id="sess-a"))
    second = await runtime.run(AgentTask(goal="b", session_id="sess-b"))

    # Distinct conversations must never share one client, even with identical options.
    assert second.metadata["sdk_process_reused"] is False
    assert second.metadata["sdk_process_start_count"] == 2
    assert second.metadata["sdk_process_reuse_scope"] == "conversation"
    assert len(FakeClaudeClient.instances) == 2

    await runtime.aclose()


@pytest.mark.asyncio
async def test_claude_reuse_shares_client_for_same_session() -> None:
    FakeClaudeClient.messages = [assistant("ok"), result_message()]
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=FakeClaudeClient,
        reuse_process=True,
    )

    await runtime.run(AgentTask(goal="a", session_id="sess-1"))
    second = await runtime.run(AgentTask(goal="b", session_id="sess-1"))

    assert second.metadata["sdk_process_reused"] is True
    assert second.metadata["sdk_process_start_count"] == 1
    assert second.metadata["sdk_process_reuse_scope"] == "conversation"
    assert len(FakeClaudeClient.instances) == 1


@pytest.mark.asyncio
async def test_claude_logs_when_close_fails_after_connect_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ExplodingClient(FakeClaudeClient):
        async def connect(self) -> None:
            raise RuntimeError("connect failed")

        async def disconnect(self) -> None:
            raise RuntimeError("close failed too")

    runtime = ClaudeAgentRuntime(
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
        client_cls=ExplodingClient,
        reuse_process=True,
    )

    with caplog.at_level(logging.WARNING):
        # The original connect error must propagate, not the secondary close error.
        with pytest.raises(RuntimeError, match="connect failed"):
            await runtime.run(AgentTask(goal="x"))

    assert "after startup failure" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        # Monotonic ladder: CAUTIOUS must never be looser than DEFAULT. Claude has no
        # distinct cautious-execution tier, so CAUTIOUS collapses to "default" (no
        # auto-approval) rather than the old "acceptEdits" (auto-approved edits).
        (PermissionMode.STRICT, "plan"),
        (PermissionMode.CAUTIOUS, "default"),
        (PermissionMode.DEFAULT, "default"),
        (PermissionMode.PERMISSIVE, "bypassPermissions"),
    ],
)
async def test_claude_builds_permission_mode(mode: PermissionMode, expected: str) -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    await runtime.run(AgentTask(goal="x", permissions=PermissionProfile(mode=mode)))

    assert RECORDED["options"].permission_mode == expected


@pytest.mark.asyncio
async def test_claude_read_only_filesystem_forces_plan_mode() -> None:
    # READ_ONLY is a hard constraint: it forces plan mode even when the permission
    # mode would otherwise allow writes. Previously filesystem was ignored entirely.
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(
        AgentTask(
            goal="x",
            permissions=PermissionProfile(
                mode=PermissionMode.PERMISSIVE, filesystem=FilesystemAccess.READ_ONLY
            ),
        )
    )

    assert RECORDED["options"].permission_mode == "plan"
    assert result.metadata["permission_mode"] == "plan"


@pytest.mark.asyncio
async def test_claude_builds_full_request(tmp_path: Path) -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    await runtime.run(
        AgentTask(
            goal="x",
            system="be careful",
            working_directory=tmp_path,
            metadata={"model": "claude-opus", "setting_sources": ["project"]},
            output_schema={"type": "object", "properties": {}},
            budget_usd=2.5,
            mcp_servers=(McpServerConfig(name="fs", command="mcp", args=("--root",)),),
            resume_from=SessionResumeState(session_id="resume-123"),
        )
    )

    options = RECORDED["options"]
    assert options.model == "claude-opus"
    assert options.system_prompt == "be careful"
    assert options.cwd == tmp_path
    assert options.resume == "resume-123"
    assert options.max_budget_usd == 2.5
    assert options.setting_sources == ["project"]
    assert options.output_format == {
        "type": "json_schema",
        "schema": {"type": "object", "properties": {}},
    }
    assert options.mcp_servers["fs"]["type"] == "stdio"


@pytest.mark.asyncio
async def test_claude_resume_from_session_id() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    await runtime.run(AgentTask(goal="x", session_id="sess-1"))

    assert RECORDED["options"].resume == "sess-1"


@pytest.mark.asyncio
async def test_claude_records_dropped_options() -> None:
    @dataclass
    class NarrowOptions:
        model: str | None = None
        allowed_tools: list[str] = field(default_factory=list)
        disallowed_tools: list[str] = field(default_factory=list)
        permission_mode: str | None = None
        # Intentionally missing cwd so it gets dropped.

    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=NarrowOptions,
    )

    result = await runtime.run(AgentTask(goal="x", working_directory=Path("/tmp")))

    assert "cwd" in result.metadata["dropped_options"]


@pytest.mark.asyncio
async def test_claude_fails_closed_when_sdk_drops_permission_mode() -> None:
    # The permission mode is the security posture: an options class that no
    # longer accepts it must fail the run, not silently use the SDK default.
    @dataclass
    class NoPermissionModeOptions:
        model: str | None = None
        allowed_tools: list[str] = field(default_factory=list)
        disallowed_tools: list[str] = field(default_factory=list)

    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=NoPermissionModeOptions,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(AgentTask(goal="x"))

    assert exc_info.value.field == "permissions"


@pytest.mark.asyncio
async def test_claude_tool_filter_drop_fails_closed_only_when_requested() -> None:
    @dataclass
    class NoToolFilterOptions:
        model: str | None = None
        permission_mode: str | None = None
        # Intentionally missing allowed_tools/disallowed_tools.

    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=NoToolFilterOptions,
    )

    # No tool filters requested: dropping the empty lists is harmless, recorded.
    result = await runtime.run(AgentTask(goal="x"))
    assert "allowed_tools" in result.metadata["dropped_options"]

    # An actual allow-list request must not be silently widened to all tools.
    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(goal="x", permissions=PermissionProfile(allowed_tools=("Read",)))
        )


@pytest.mark.asyncio
async def test_claude_streams_events_before_completion() -> None:
    sink = RecordingEventSink()
    messages = [
        assistant(
            "partial",
            tools=[{"type": "tool_use", "id": "tool-1", "name": "Write", "input": {"p": "x"}}],
            session_id="s",
        ),
        {
            "type": "UserMessage",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "tool-1",
                    "content": "boom",
                    "is_error": True,
                }
            ],
        },
        result_message(total_cost_usd=0.0),
    ]
    runtime = ClaudeAgentRuntime(
        query_func=make_query(messages), options_cls=FakeClaudeOptions
    )

    result = await runtime.run(AgentTask(goal="x", event_sink=sink))

    names = [event["name"] for event in sink.events]
    completed_index = names.index("agent.task.completed")
    # The delta and tool events must arrive before task.completed, not be replayed after.
    assert names.index("agent.output.delta") < completed_index
    assert names.index("agent.tool.requested") < completed_index
    assert names.index("agent.tool.completed") < completed_index
    # tool_completed matched via tool_use_id carries the error status.
    tool_completed = sink.events[names.index("agent.tool.completed")]
    assert tool_completed["attributes"]["tool_name"] == "Write"
    assert tool_completed["attributes"]["status"] == "error"
    # And the final audit reflects the matched error status.
    assert result.tool_calls[0].status == "error"


@pytest.mark.asyncio
async def test_claude_emits_single_delta_when_result_only() -> None:
    sink = RecordingEventSink()
    runtime = ClaudeAgentRuntime(
        query_func=make_query([result_message(result="final text", total_cost_usd=0.0)]),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(AgentTask(goal="x", event_sink=sink))

    names = [event["name"] for event in sink.events]
    assert result.output == "final text"
    assert names.count("agent.output.delta") == 1


@pytest.mark.asyncio
async def test_claude_max_turns_finish_reason() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query(
            [
                assistant("worked a lot", session_id="s"),
                result_message(is_error=True, subtype="error_max_turns", total_cost_usd=0.0),
            ]
        ),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "max_turns"
    assert result.error is not None


@pytest.mark.asyncio
async def test_claude_result_tool_calls_carry_result_preview() -> None:
    messages = [
        {
            "type": "AssistantMessage",
            "content": [
                {"type": "text", "text": "run"},
                {"type": "tool_use", "id": "t1", "name": "Read", "input": {"path": "x"}},
            ],
        },
        {
            "type": "UserMessage",
            "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "file body"},
            ],
        },
        result_message(),
    ]
    runtime = ClaudeAgentRuntime(query_func=make_query(messages), options_cls=FakeClaudeOptions)

    result = await runtime.run(AgentTask(goal="x"))

    # result.tool_calls now carry the same preview the streamed events do.
    assert result.tool_calls[0].tool_name == "Read"
    assert result.tool_calls[0].result_preview == "file body"
    assert result.tool_calls[0].status == "ok"


@pytest.mark.asyncio
async def test_claude_unsatisfied_output_schema_fails() -> None:
    # Schema requested, no native structured output, text is not JSON -> failure,
    # matching Codex/Antigravity instead of silently succeeding with None.
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("not json at all"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(AgentTask(goal="x", output_schema={"type": "object"}))

    assert result.finish_reason == "failed"
    assert result.parsed_output is None
    assert "output_schema" in (result.error or "")


@pytest.mark.asyncio
async def test_claude_empty_completion_fails() -> None:
    # No text, no tool calls, no structured output -> nothing usable -> failure.
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant(""), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert result.error is not None


@pytest.mark.asyncio
async def test_claude_failed_uses_errors_list() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query(
            [result_message(is_error=True, subtype="error", errors=["nope", "stop"])]
        ),
        options_cls=FakeClaudeOptions,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert result.error == "nope; stop"


@pytest.mark.asyncio
async def test_claude_rejects_network() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="x", permissions=PermissionProfile(network=True)))


@pytest.mark.asyncio
async def test_claude_runtime_rejects_unsupported_model() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
        supported_models=("claude-sonnet-4-6",),
    )

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(AgentTask(goal="x", metadata={"model": "unsupported"}))


def test_claude_availability_uses_injected_sdk() -> None:
    runtime = ClaudeAgentRuntime(
        query_func=make_query([]), options_cls=FakeClaudeOptions
    )

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.CLAUDE_AGENT_SDK
    assert diagnostic.available is True


@pytest.mark.asyncio
async def test_claude_passes_runtime_env_for_bedrock() -> None:
    runtime = ClaudeAgentRuntime(
        env={"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_PROFILE": "agent-runtime-kit"},
        query_func=make_query([assistant("ok"), result_message()]),
        options_cls=FakeClaudeOptions,
    )

    await runtime.run(AgentTask(goal="x"))

    assert RECORDED["options"].env == {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_PROFILE": "agent-runtime-kit",
    }


def test_claude_availability_reports_bedrock_auth_source() -> None:
    runtime = ClaudeAgentRuntime(
        env={
            "CLAUDE_CODE_USE_BEDROCK": "1",
            "AWS_PROFILE": "agent-runtime-kit",
            "AWS_REGION": "us-east-1",
        },
        query_func=make_query([]),
        options_cls=FakeClaudeOptions,
    )

    diagnostic = runtime.availability()

    assert diagnostic.available is True
    assert diagnostic.metadata["auth_source"] == "amazon-bedrock"
    assert diagnostic.metadata["credential_chain"] == "aws-sdk"
    assert diagnostic.metadata["aws_profile_configured"] is True
