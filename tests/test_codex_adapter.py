from __future__ import annotations

import asyncio
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

    async def run(
        self,
        prompt: str,
        *,
        approval_mode: Any = None,
        sandbox: Any = None,
        **kwargs: Any,
    ) -> Any:
        kwargs.update(approval_mode=approval_mode, sandbox=sandbox)
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
    enter_errors: list[BaseException | None] = []
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
        if FakeCodex.enter_errors:
            error = FakeCodex.enter_errors.pop(0)
            if error is not None:
                raise error
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def thread_start(
        self, *, approval_mode: Any = None, sandbox: Any = None, **kwargs: Any
    ) -> FakeThread:
        kwargs.update(approval_mode=approval_mode, sandbox=sandbox)
        FakeCodex.last_started_kwargs = kwargs
        return FakeThread("thread-new", self._run_result, self._run_error)

    async def thread_resume(
        self,
        thread_id: str,
        *,
        approval_mode: Any = None,
        sandbox: Any = None,
        **kwargs: Any,
    ) -> FakeThread:
        kwargs.update(approval_mode=approval_mode, sandbox=sandbox)
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
    FakeCodex.enter_errors = []
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
    # 4 raw input tokens minus 1 cached: input_tokens excludes cache reads.
    assert result.usage.input_tokens == 3
    assert result.usage.cache_read_tokens == 1
    assert result.session_id == "thread-new"
    assert sink.events[-1]["name"] == "agent.task.completed"


@pytest.mark.asyncio
async def test_codex_rejects_structured_output_that_misses_schema() -> None:
    runtime = make_runtime(
        {
            "status": "completed",
            "final_response": '{"ok": "bad"}',
            "usage": {
                "total": {
                    "input_tokens": 4,
                    "output_tokens": 2,
                    "cached_input_tokens": 0,
                }
            },
        }
    )
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "integer"}},
        "required": ["ok"],
    }

    result = await runtime.run(AgentTask(goal="x", output_schema=schema))

    assert result.finish_reason == "failed"
    assert "does not conform" in (result.error or "")
    assert result.parsed_output_available is False
    assert result.session_id == "thread-new"
    assert result.usage.input_tokens == 4


@pytest.mark.asyncio
async def test_codex_accepts_textual_json_null() -> None:
    runtime = make_runtime({"status": "completed", "final_response": "null"})

    result = await runtime.run(AgentTask(goal="x", output_schema={"type": "null"}))

    assert result.finish_reason == "done"
    assert result.parsed_output is None
    assert result.parsed_output_available is True


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
async def test_codex_aclose_waits_for_in_flight_run() -> None:
    runtime = make_runtime(reuse_process=True)

    # Holding the run lock simulates an in-flight run(); aclose() must block on it
    # rather than racing in and closing the shared app-server mid-turn.
    await runtime._codex_run_lock.acquire()
    try:
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(runtime.aclose(), timeout=0.05)
    finally:
        runtime._codex_run_lock.release()

    # Once the run releases the lock, aclose() proceeds.
    await asyncio.wait_for(runtime.aclose(), timeout=1.0)


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
async def test_codex_runtime_evicts_reused_process_after_cancellation() -> None:
    # CancelledError is a BaseException; the reuse cleanup must still evict the
    # interrupted app-server so the next run() does not inherit a poisoned process.
    errors: list[BaseException | None] = [asyncio.CancelledError(), None]

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return FakeCodex(config, run_error=errors.pop(0))

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
        reuse_process=True,
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.run(AgentTask(goal="cancelled"))

    assert FakeCodex.instances[0].closed is True

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert len(FakeCodex.instances) == 2

    await runtime.aclose()


@pytest.mark.asyncio
async def test_codex_queued_run_cancel_does_not_evict_in_flight_client() -> None:
    # Regression for the run-lock/eviction ordering: a run() cancelled while it is
    # still QUEUED on the run lock must not evict the client the in-flight holder is
    # using. This fails if the try/except that evicts on cancellation wraps the lock
    # acquisition (a queued waiter's CancelledError would then close the holder's
    # shared app-server); it passes when eviction happens inside the held lock.
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingThread(FakeThread):
        async def run(
            self,
            prompt: str,
            *,
            approval_mode: Any = None,
            sandbox: Any = None,
            **kwargs: Any,
        ) -> Any:
            del approval_mode, sandbox, kwargs
            entered.set()
            await release.wait()
            return {"status": "completed", "final_response": f"done: {prompt}"}

    class BlockingCodex(FakeCodex):
        async def thread_start(
            self, *, approval_mode: Any = None, sandbox: Any = None, **kwargs: Any
        ) -> FakeThread:
            del approval_mode, sandbox, kwargs
            return BlockingThread("thread-new")

        async def thread_resume(
            self,
            thread_id: str,
            *,
            approval_mode: Any = None,
            sandbox: Any = None,
            **kwargs: Any,
        ) -> FakeThread:
            del approval_mode, sandbox, kwargs
            return BlockingThread(thread_id)

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return BlockingCodex(config)

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
        reuse_process=True,
    )

    task_a = asyncio.create_task(runtime.run(AgentTask(goal="A")))
    await asyncio.wait_for(entered.wait(), timeout=1.0)  # A holds the run lock, mid-run

    task_b = asyncio.create_task(runtime.run(AgentTask(goal="B")))
    for _ in range(1000):
        await asyncio.sleep(0)
        if runtime._codex_run_lock.locked() and runtime._codex_run_lock._waiters:
            break
    assert runtime._codex_run_lock._waiters, "precondition: B must be queued on the run lock"

    task_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_b

    # B's queued cancellation must not have touched A's shared client.
    assert len(FakeCodex.instances) == 1, "B must not have started a second app-server"
    assert FakeCodex.instances[0].closed is False

    release.set()
    result = await asyncio.wait_for(task_a, timeout=1.0)
    assert result.error is None
    assert result.output == "done: A"
    assert FakeCodex.instances[0].closed is False

    await runtime.aclose()


@pytest.mark.asyncio
async def test_codex_runtime_closes_context_after_enter_failure() -> None:
    FakeCodex.enter_errors = [RuntimeError("enter failed"), None]
    runtime = make_runtime(reuse_process=True)

    with pytest.raises(RuntimeError, match="enter failed"):
        await runtime.run(AgentTask(goal="fail"))

    recovered = await runtime.run(AgentTask(goal="recover"))

    assert recovered.output == "done: recover"
    assert recovered.metadata["sdk_process_reused"] is False
    assert recovered.metadata["sdk_process_start_count"] == 1
    assert len(FakeCodex.instances) == 2
    assert FakeCodex.instances[0].closed is True
    assert FakeCodex.instances[1].closed is False

    await runtime.aclose()

    assert FakeCodex.instances[1].closed is True


@pytest.mark.asyncio
async def test_codex_logs_when_close_fails_after_enter_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    class ExplodingCodex(FakeCodex):
        async def __aenter__(self) -> FakeCodex:
            raise RuntimeError("enter failed")

        async def __aexit__(self, *args: object) -> None:
            raise RuntimeError("close failed too")

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return ExplodingCodex(config)

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
        reuse_process=True,
    )

    with caplog.at_level(logging.WARNING):
        # The original enter error must propagate, not the secondary close error.
        with pytest.raises(RuntimeError, match="enter failed"):
            await runtime.run(AgentTask(goal="x"))

    assert "after startup failure" in caplog.text


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
async def test_codex_prefers_typed_model_and_effort_fields() -> None:
    runtime = make_runtime()

    await runtime.run(
        AgentTask(goal="x", model="gpt-5.5-codex", reasoning_effort="high"),
    )

    assert FakeThread.last_run_kwargs is not None
    assert FakeThread.last_run_kwargs["model"] == "gpt-5.5-codex"
    assert FakeThread.last_run_kwargs["effort"] == "high"


@pytest.mark.asyncio
async def test_codex_typed_model_field_overrides_metadata_alias() -> None:
    runtime = make_runtime()

    await runtime.run(
        AgentTask(goal="x", model="from-field", metadata={"model": "from-metadata"}),
    )

    assert FakeThread.last_run_kwargs is not None
    assert FakeThread.last_run_kwargs["model"] == "from-field"


@pytest.mark.asyncio
async def test_codex_tolerates_config_option_drift() -> None:
    # A config class that no longer accepts 'config_overrides' must not crash the
    # run; the dropped option is recorded like the Claude adapter does.
    @dataclass
    class NarrowConfig:
        cwd: str | None = None
        env: dict[str, str] | None = None

    def codex_factory(*, config: NarrowConfig) -> FakeCodex:
        return FakeCodex(config)  # type: ignore[arg-type]

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=NarrowConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "done"
    assert "config_overrides" in result.metadata["dropped_options"]


class RestrictedRunThread:
    """Thread whose run() signature lost the sandbox/approval_mode params."""

    def __init__(self) -> None:
        self.id = "thread-restricted"

    async def run(self, prompt: str, *, cwd: str | None = None, model: str | None = None) -> Any:
        raise AssertionError("run() must not be reached when the sandbox cannot be honored")


class RestrictedRunCodex(FakeCodex):
    async def thread_start(
        self, *, approval_mode: Any = None, sandbox: Any = None, **kwargs: Any
    ) -> Any:
        kwargs.update(approval_mode=approval_mode, sandbox=sandbox)
        FakeCodex.last_started_kwargs = kwargs
        return RestrictedRunThread()


@pytest.mark.asyncio
async def test_codex_fails_closed_when_sdk_drops_sandbox_from_run() -> None:
    # Security posture must never be best-effort: if the installed SDK's
    # thread.run() no longer accepts sandbox/approval_mode, refuse to run
    # instead of silently executing under the SDK default sandbox.
    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return RestrictedRunCodex(config)

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(
            AgentTask(
                goal="restricted",
                permissions=PermissionProfile(
                    mode=PermissionMode.STRICT,
                    filesystem=FilesystemAccess.READ_ONLY,
                ),
            )
        )

    assert exc_info.value.field == "permissions"


class RestrictedStartCodex(FakeCodex):
    async def thread_start(
        self,
        *,
        cwd: str | None = None,
        developer_instructions: str | None = None,
        model: str | None = None,
    ) -> Any:
        raise AssertionError("thread_start() must not be reached without a sandbox")


@pytest.mark.asyncio
async def test_codex_fails_closed_when_sdk_drops_sandbox_from_thread_start() -> None:
    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        return RestrictedStartCodex(config)

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(AgentTask(goal="restricted"))

    assert exc_info.value.field == "permissions"


@pytest.mark.asyncio
async def test_codex_emits_tool_events() -> None:
    run_result = {
        "status": "completed",
        "final_response": "done",
        "items": [
            {"type": "commandExecution", "command": "ls", "aggregated_output": "ok"},
        ],
    }
    sink = RecordingEventSink()
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x", event_sink=sink))

    names = [event["name"] for event in sink.events]
    # Codex now emits tool events (parsed from the TurnResult) like the streaming
    # adapters, not just result.tool_calls.
    assert "agent.tool.requested" in names
    assert "agent.tool.completed" in names
    assert result.tool_calls[0].tool_name == "command"


@pytest.mark.asyncio
async def test_codex_usage_prefers_per_turn_over_cumulative() -> None:
    # A resumed thread reports cumulative 'total'; the per-turn 'last' is what this
    # turn actually cost and must be preferred so usage/cost are not over-counted.
    run_result = {
        "status": "completed",
        "final_response": "ok",
        "usage": {
            "total": {"input_tokens": 1000, "output_tokens": 2000, "total_tokens": 3000},
            "last": {
                "input_tokens": 4,
                "output_tokens": 6,
                "cached_input_tokens": 0,
                "total_tokens": 10,
            },
        },
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    assert result.usage.input_tokens == 4
    assert result.usage.output_tokens == 6
    assert result.usage.total_tokens == 10


@pytest.mark.asyncio
async def test_codex_usage_is_unknown_when_sdk_omits_breakdown() -> None:
    runtime = make_runtime({"status": "completed", "final_response": "ok"})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.usage.input_tokens is None
    assert result.usage.output_tokens is None
    assert result.usage.total_tokens is None
    assert result.usage.cost_usd is None


@pytest.mark.asyncio
async def test_codex_partial_usage_preserves_unknown_cache_and_normalized_input() -> None:
    partial_runtime = make_runtime(
        {
            "status": "completed",
            "final_response": "ok",
            "usage": {"total": {"input_tokens": 4, "output_tokens": 2}},
        }
    )
    partial = await partial_runtime.run(AgentTask(goal="x"))

    assert partial.usage.input_tokens is None
    assert partial.usage.cache_read_tokens is None
    assert partial.usage.output_tokens == 2
    assert partial.usage.total_tokens == 6

    zero_runtime = make_runtime(
        {
            "status": "completed",
            "final_response": "ok",
            "usage": {
                "total": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cached_input_tokens": 0,
                    "total_tokens": 0,
                }
            },
        }
    )
    zero = await zero_runtime.run(AgentTask(goal="x"))

    assert zero.usage.input_tokens == 0
    assert zero.usage.output_tokens == 0
    assert zero.usage.cache_read_tokens == 0
    assert zero.usage.total_tokens == 0


@pytest.mark.asyncio
async def test_codex_session_id_falls_back_to_task_when_sdk_omits_it() -> None:
    # Thread with a falsy id -> result session_id should still reflect the resume id.
    run_result = {"status": "completed", "final_response": "ok"}

    def codex_factory(*, config: FakeCodexConfig) -> FakeCodex:
        codex = FakeCodex(config, run_result)
        original_resume = codex.thread_resume

        async def resume_without_id(
            thread_id: str,
            *,
            approval_mode: Any = None,
            sandbox: Any = None,
            **kwargs: Any,
        ) -> FakeThread:
            thread = await original_resume(
                thread_id, approval_mode=approval_mode, sandbox=sandbox, **kwargs
            )
            thread.id = ""
            return thread

        codex.thread_resume = resume_without_id  # type: ignore[method-assign]
        return codex

    runtime = CodexAgentRuntime(
        codex_cls=codex_factory,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    result = await runtime.run(AgentTask(goal="x", session_id="thread-123"))

    assert result.session_id == "thread-123"


@pytest.mark.asyncio
async def test_codex_empty_output_with_tool_calls_succeeds() -> None:
    # Empty final_response is fine when the turn did real tool work; only a
    # completion with nothing usable is a failure (matches Claude/Antigravity).
    run_result = {
        "status": "completed",
        "final_response": "",
        "items": [
            {"type": "commandExecution", "command": "ls", "aggregated_output": "ok"},
        ],
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "done"
    assert result.output == ""
    assert result.tool_calls[0].tool_name == "command"


@pytest.mark.asyncio
async def test_codex_empty_completion_without_tool_calls_fails() -> None:
    runtime = make_runtime({"status": "completed", "final_response": "", "items": []})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert result.rounds == 1
    assert result.error is not None


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
async def test_codex_in_progress_status_fails_closed() -> None:
    # The real SDK's TurnStatus includes the non-terminal "inProgress"; a run that
    # ends in it must not report success with partial output.
    runtime = make_runtime({"status": "inProgress", "final_response": "partial"})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert "inProgress" in (result.error or "")


@pytest.mark.asyncio
async def test_codex_unknown_status_fails_closed() -> None:
    # A status value added by a future SDK must fail closed, not read as success.
    runtime = make_runtime({"status": "cancelled", "final_response": "partial"})

    result = await runtime.run(AgentTask(goal="x"))

    assert result.finish_reason == "failed"
    assert "cancelled" in (result.error or "")


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
async def test_codex_usage_excludes_cached_from_input_tokens() -> None:
    run_result = {
        "status": "completed",
        "final_response": "done",
        "usage": {"total": {"input_tokens": 10, "output_tokens": 5, "cached_input_tokens": 4}},
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    # OpenAI counts cached input inside input_tokens; the Usage contract reports
    # cache reads separately, so the 4 cached tokens must not appear in both fields.
    assert result.usage.input_tokens == 6
    assert result.usage.cache_read_tokens == 4
    # Fallback total keeps vendor semantics: raw input (incl. cached) + output.
    assert result.usage.total_tokens == 15


@pytest.mark.asyncio
async def test_codex_usage_clamps_when_cached_exceeds_input() -> None:
    # Defensive clamp: a vendor bug reporting cached > input must not produce a
    # negative input_tokens.
    run_result = {
        "status": "completed",
        "final_response": "done",
        "usage": {"total": {"input_tokens": 2, "output_tokens": 5, "cached_input_tokens": 7}},
    }
    runtime = make_runtime(run_result)

    result = await runtime.run(AgentTask(goal="x"))

    assert result.usage.input_tokens == 0
    assert result.usage.cache_read_tokens == 7


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
