from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_runtime_kit import (
    AgentKit,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    FakeAgentRuntime,
    FilesystemAccess,
    ParsedResult,
    PermissionMode,
    PermissionProfile,
    RuntimeAvailability,
)
from agent_runtime_kit._schema import json_schema_for
from agent_runtime_kit.testing import RecordingEventSink


@dataclass
class Point:
    x: int
    y: int


class RecordingRuntime:
    """Protocol-complete runtime that records the task and returns a canned result."""

    kind = AgentRuntimeKind.FAKE
    capabilities = FakeAgentRuntime().capabilities

    def __init__(self, result: AgentResult | None = None) -> None:
        self.task: AgentTask | None = None
        self.closed = False
        self._result = result or AgentResult(output="recorded")

    def availability(self) -> RuntimeAvailability:
        return RuntimeAvailability.ok(self.kind)

    async def run(self, task: AgentTask) -> AgentResult:
        self.task = task
        return self._result

    async def cancel(self, task_id: str) -> None:
        del task_id

    async def aclose(self) -> None:
        self.closed = True

    async def __aenter__(self) -> RecordingRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()


@pytest.mark.asyncio
async def test_kit_runs_registered_fake_by_kind() -> None:
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run("fake", goal="hello")

    assert result.finish_reason == "done"
    assert result.output


@pytest.mark.asyncio
async def test_kit_kwargs_map_onto_task_fields() -> None:
    runtime = RecordingRuntime()
    kit = AgentKit(register_default_adapters=False)

    await kit.run(
        runtime,
        goal="g",
        system="be careful",
        model="model-x",
        reasoning_effort="high",
        working_directory="/tmp/ws",
        session_id="sess-1",
        budget_usd=1.5,
        task_id="task-1",
        metadata={"stage": "demo"},
    )

    task = runtime.task
    assert task is not None
    assert task.goal == "g"
    assert task.system == "be careful"
    assert task.model == "model-x"
    assert task.reasoning_effort == "high"
    assert task.working_directory == Path("/tmp/ws")
    assert task.session_id == "sess-1"
    assert task.budget_usd == 1.5
    assert task.task_id == "task-1"
    assert task.metadata["stage"] == "demo"


@pytest.mark.asyncio
async def test_kit_permission_literals_reach_adapters_as_enum_members() -> None:
    runtime = RecordingRuntime()
    kit = AgentKit(register_default_adapters=False)

    await kit.run(
        runtime,
        goal="g",
        permissions="strict",
        filesystem="read-only",
        allowed_tools=("view_file",),
    )

    task = runtime.task
    assert task is not None
    # Identity: the adapters' `is` checks must see real members.
    assert task.permissions.mode is PermissionMode.STRICT
    assert task.permissions.filesystem is FilesystemAccess.READ_ONLY
    assert task.permissions.allowed_tools == ("view_file",)


@pytest.mark.asyncio
async def test_kit_rejects_profile_alongside_tool_kwargs() -> None:
    kit = AgentKit(register_default_adapters=False)

    with pytest.raises(ValueError, match="inside the"):
        await kit.run(
            RecordingRuntime(),
            goal="g",
            permissions=PermissionProfile(),
            allowed_tools=("view_file",),
        )


@pytest.mark.asyncio
async def test_kit_requires_exactly_one_of_goal_and_task() -> None:
    kit = AgentKit(register_default_adapters=False)

    with pytest.raises(ValueError, match="either goal"):
        await kit.run(RecordingRuntime())

    with pytest.raises(ValueError, match="mutually exclusive"):
        await kit.run(RecordingRuntime(), task=AgentTask(goal="a"), goal="b")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("goal", None),
        ("system", None),
        ("allowed_tools", ()),
        ("metadata", {}),
        ("budget_usd", None),
        ("sdk_executions", 1),
    ],
)
async def test_kit_rejects_explicit_default_values_with_prebuilt_task(
    field_name: str, value: object
) -> None:
    kit = AgentKit(register_default_adapters=False)

    # Explicit default-valued kwargs are still caller input. Silently ignoring
    # them makes wrapper code look as though it changed the prebuilt task.
    with pytest.raises(ValueError, match=field_name):
        await kit.run(RecordingRuntime(), task=AgentTask(goal="a"), **{field_name: value})


@pytest.mark.asyncio
async def test_kit_task_passthrough_runs_unchanged() -> None:
    runtime = RecordingRuntime()
    kit = AgentKit(register_default_adapters=False)
    task = AgentTask(goal="prebuilt", session_id="keep-me")

    await kit.run(runtime, task=task)

    assert runtime.task is task


def test_kit_aliases_resolve_builtin_kinds() -> None:
    kit = AgentKit()

    # Adapters register lazily and are constructible without vendor SDKs.
    assert kit.capabilities_for("claude").streaming is True
    assert kit.capabilities_for("codex").streaming is False
    # Exact kind strings keep working; unknown aliases are not invented.
    assert kit.capabilities_for("claude-agent-sdk").streaming is True
    assert set(kit.kinds()) >= {
        AgentRuntimeKind.FAKE,
        AgentRuntimeKind.CLAUDE_AGENT_SDK,
        AgentRuntimeKind.CODEX_AGENT_SDK,
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
    }
    assert len(kit.availability()) == len(kit.kinds())


@pytest.mark.asyncio
async def test_kit_output_type_generates_schema_and_parses_result() -> None:
    runtime = RecordingRuntime(
        AgentResult(output='{"x": 1, "y": 2}', parsed_output={"x": 1, "y": 2})
    )
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run(runtime, goal="point please", output_type=Point)

    assert isinstance(result, ParsedResult)
    assert result.parsed == Point(x=1, y=2)
    assert result.parsed_output == Point(x=1, y=2)
    # The wire schema came from the type, not hand-written JSON schema.
    task = runtime.task
    assert task is not None
    assert dict(task.output_schema or {}) == json_schema_for(Point)


@pytest.mark.asyncio
async def test_kit_output_type_mismatch_fails_the_result_not_the_call() -> None:
    runtime = RecordingRuntime(
        AgentResult(output='{"x": "no"}', parsed_output={"x": "no", "y": 2})
    )
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run(runtime, goal="point please", output_type=Point)

    assert result.finish_reason == "failed"
    assert result.error is not None and "Point" in result.error
    assert result.parsed is None


@pytest.mark.asyncio
async def test_kit_output_type_passes_adapter_failures_through() -> None:
    failed = AgentResult(output="", finish_reason="failed", error="vendor exploded")
    runtime = RecordingRuntime(failed)
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run(runtime, goal="g", output_type=Point)

    assert result.finish_reason == "failed"
    assert result.error == "vendor exploded"
    assert result.parsed is None


@pytest.mark.asyncio
async def test_kit_output_type_never_leaks_failed_raw_payload() -> None:
    failed = AgentResult(
        output='{"x": "unchecked"}',
        finish_reason="failed",
        error="vendor rejected structured output",
        parsed_output={"x": "unchecked"},
    )
    runtime = RecordingRuntime(failed)
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run(runtime, goal="g", output_type=Point)

    assert result.finish_reason == "failed"
    assert result.error == "vendor rejected structured output"
    assert result.parsed_output is None
    assert result.parsed is None


@pytest.mark.asyncio
async def test_kit_output_type_clears_raw_payload_for_failed_reason_without_error() -> None:
    failed = AgentResult(
        output='{"x": 1, "y": 2}',
        finish_reason="failed",
        parsed_output={"x": 1, "y": 2},
    )
    runtime = RecordingRuntime(failed)

    result = await AgentKit(register_default_adapters=False).run(
        runtime, goal="g", output_type=Point
    )

    assert result.finish_reason == "failed"
    assert result.error is None
    assert result.parsed_output is None


@pytest.mark.asyncio
async def test_kit_output_type_and_output_schema_are_exclusive() -> None:
    kit = AgentKit(register_default_adapters=False)

    with pytest.raises(ValueError, match="mutually exclusive"):
        await kit.run(
            RecordingRuntime(),
            goal="g",
            output_type=Point,
            output_schema={"type": "object"},
        )


@pytest.mark.asyncio
async def test_kit_output_type_composes_with_task_passthrough() -> None:
    runtime = RecordingRuntime(AgentResult(output="{}", parsed_output={"x": 3, "y": 4}))
    kit = AgentKit(register_default_adapters=False)

    result = await kit.run(runtime, task=AgentTask(goal="prebuilt"), output_type=Point)

    assert result.parsed == Point(x=3, y=4)
    task = runtime.task
    assert task is not None
    assert task.output_schema is not None  # schema injected into the prebuilt task


@pytest.mark.asyncio
async def test_kit_on_handlers_filter_compose_and_never_break_runs() -> None:
    kit = AgentKit(register_default_adapters=False)
    completed: list[str] = []
    everything: list[str] = []
    awaited: list[str] = []
    user_sink = RecordingEventSink()

    @kit.on("agent.task.completed")
    def only_completed(event: Mapping[str, Any]) -> None:
        completed.append(str(event["name"]))

    @kit.on()
    def wildcard(event: Mapping[str, Any]) -> None:
        everything.append(str(event["name"]))

    @kit.on("agent.task.started")
    async def async_handler(event: Mapping[str, Any]) -> None:
        awaited.append(str(event["name"]))

    @kit.on()
    def exploding(event: Mapping[str, Any]) -> None:
        raise RuntimeError("handler bug")

    result = await kit.run("fake", goal="observe me", event_sink=user_sink)

    assert result.finish_reason == "done"
    assert completed == ["agent.task.completed"]
    assert awaited == ["agent.task.started"]
    # The fake runtime emits the full normalized sequence; wildcard saw it all.
    assert everything[0] == "agent.task.started"
    assert everything[-1] == "agent.task.completed"
    # The raising handler broke neither the run, the other handlers, nor the
    # user's own sink.
    assert [event["name"] for event in user_sink.events] == everything


@pytest.mark.asyncio
async def test_kit_on_handlers_apply_to_prebuilt_tasks() -> None:
    runtime = RecordingRuntime()
    kit = AgentKit(register_default_adapters=False)
    seen: list[str] = []

    @kit.on()
    def watch(event: Mapping[str, Any]) -> None:
        seen.append(str(event["name"]))

    await kit.run(runtime, task=AgentTask(goal="prebuilt"))

    task = runtime.task
    assert task is not None
    assert task.event_sink is not None  # tee injected into the passthrough task
    await task.event_sink.emit({"name": "agent.task.started"})
    assert seen == ["agent.task.started"]


@pytest.mark.asyncio
async def test_kit_runtime_decorator_registers_factory() -> None:
    kit = AgentKit(register_default_adapters=False)

    @kit.runtime("x-third-party")
    def factory(**_: Any) -> FakeAgentRuntime:
        return FakeAgentRuntime(output="third-party output")

    result = await kit.run("x-third-party", goal="g")

    assert result.output == "third-party output"
    assert "x-third-party" in kit.kinds()
    # Zero-arg constructibility keeps diagnostics working.
    assert kit.availability_for("x-third-party").available is True


@pytest.mark.asyncio
async def test_kit_caches_runtimes_per_kind_and_closes_them() -> None:
    constructed: list[RecordingRuntime] = []

    def factory(**_: Any) -> RecordingRuntime:
        runtime = RecordingRuntime()
        constructed.append(runtime)
        return runtime

    async with AgentKit(register_default_adapters=False) as kit:
        kit.registry.register("x-counting", factory)
        await kit.run("x-counting", goal="one")
        await kit.run("x-counting", goal="two")
        assert len(constructed) == 1

    assert constructed[0].closed is True
    # A directly passed instance is the caller's to manage: never closed by the hub.
    outside = RecordingRuntime()
    async with AgentKit(register_default_adapters=False) as kit:
        await kit.run(outside, goal="g")
    assert outside.closed is False


@pytest.mark.asyncio
async def test_kit_aclose_attempts_every_cached_runtime_and_reraises_first_error() -> None:
    closed: list[str] = []

    class CloseTrackingRuntime(RecordingRuntime):
        def __init__(self, name: str, error: Exception | None = None) -> None:
            super().__init__()
            self.name = name
            self.error = error

        async def aclose(self) -> None:
            closed.append(self.name)
            if self.error is not None:
                raise self.error

    kit = AgentKit(register_default_adapters=False)
    kit.registry.register("x-first", lambda: CloseTrackingRuntime("first", ValueError("first")))
    kit.registry.register(
        "x-second", lambda: CloseTrackingRuntime("second", RuntimeError("second"))
    )
    kit.registry.register("x-third", lambda: CloseTrackingRuntime("third"))
    await kit.run("x-first", goal="one")
    await kit.run("x-second", goal="two")
    await kit.run("x-third", goal="three")

    with pytest.raises(ValueError, match="first"):
        await kit.aclose()

    assert closed == ["first", "second", "third"]
    # The cache is cleared before closing, so a retry cannot close any runtime twice.
    await kit.aclose()
    assert closed == ["first", "second", "third"]
