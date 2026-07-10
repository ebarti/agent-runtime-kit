from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from agent_runtime_kit import (
    AgentKit,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    AgentTaskTimeoutError,
    CancellationDisposition,
    CancellationReceipt,
    FakeAgentRuntime,
    FinishReason,
    RuntimeAvailability,
    RuntimeRegistry,
)
from agent_runtime_kit.testing import RecordingEventSink


class LegacyBlockingRuntime:
    """A pre-receipt third-party runtime used to verify compatibility."""

    kind = AgentRuntimeKind.FAKE
    capabilities = FakeAgentRuntime().capabilities

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.run_calls = 0
        self.cancel_calls = 0
        self.cancelled = False

    def availability(self) -> RuntimeAvailability:
        return RuntimeAvailability.ok(self.kind)

    async def run(self, task: AgentTask) -> AgentResult:
        del task
        self.run_calls += 1
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return AgentResult(output="done")

    async def cancel(self, task_id: str) -> None:
        del task_id
        self.cancel_calls += 1

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> LegacyBlockingRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()


class BlockingFakeRuntime(FakeAgentRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def _run_task(self, task: AgentTask) -> AgentResult:
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return AgentResult(output=task.goal)


class SerialFakeRuntime(FakeAgentRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.lock = asyncio.Lock()
        self.holder_started = asyncio.Event()
        self.release_holder = asyncio.Event()
        self.holder_cancelled = False

    async def _run_task(self, task: AgentTask) -> AgentResult:
        async with self.lock:
            if task.goal == "holder":
                self.holder_started.set()
                try:
                    await self.release_holder.wait()
                except asyncio.CancelledError:
                    self.holder_cancelled = True
                    raise
            return AgentResult(output=task.goal)


def test_deadline_requires_timezone_and_normalizes_to_utc() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        AgentTask(goal="x", deadline=datetime(2030, 1, 1))

    plus_two = timezone(timedelta(hours=2))
    task = AgentTask(goal="x", deadline=datetime(2030, 1, 1, 12, tzinfo=plus_two))

    assert task.deadline == datetime(2030, 1, 1, 10, tzinfo=timezone.utc)


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
@pytest.mark.asyncio
async def test_kit_rejects_invalid_timeouts(timeout: float) -> None:
    kit = AgentKit(register_default_adapters=False)

    with pytest.raises(ValueError, match="timeout"):
        await kit.run(FakeAgentRuntime(), goal="x", timeout=timeout)


@pytest.mark.asyncio
async def test_timeout_and_existing_deadline_are_mutually_exclusive() -> None:
    task = AgentTask(
        goal="x",
        deadline=datetime.now(tz=timezone.utc) + timedelta(seconds=5),
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        await AgentKit(register_default_adapters=False).run(
            FakeAgentRuntime(), task=task, timeout=1
        )


@pytest.mark.asyncio
async def test_expired_deadline_never_invokes_legacy_runtime() -> None:
    runtime = LegacyBlockingRuntime()
    sink = RecordingEventSink()
    deadline = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

    with pytest.raises(AgentTaskTimeoutError) as caught:
        await AgentKit(register_default_adapters=False).run(
            runtime,
            goal="too late",
            task_id="expired",
            deadline=deadline,
            event_sink=sink,
        )

    assert runtime.run_calls == 0
    assert caught.value.task_id == "expired"
    assert caught.value.deadline == deadline
    assert isinstance(caught.value, TimeoutError)
    assert [event["name"] for event in sink.events] == [
        "agent.task.started",
        "agent.task.failed",
    ]
    assert sink.events[-1]["attributes"]["finish_reason"] == FinishReason.TIMED_OUT


@pytest.mark.asyncio
async def test_kit_timeout_cancels_legacy_runtime_and_next_run_recovers() -> None:
    runtime = LegacyBlockingRuntime()
    kit = AgentKit(register_default_adapters=False)

    with pytest.raises(AgentTaskTimeoutError):
        await kit.run(runtime, goal="slow", timeout=0.01)

    assert runtime.cancelled is True
    runtime.started.clear()
    runtime.release.set()
    result = await kit.run(runtime, goal="next", timeout=1)
    assert result.output == "done"


@pytest.mark.asyncio
async def test_kit_cancels_cached_runtime_without_constructing_another() -> None:
    runtime = LegacyBlockingRuntime()
    constructions = 0

    def factory() -> LegacyBlockingRuntime:
        nonlocal constructions
        constructions += 1
        return runtime

    registry = RuntimeRegistry()
    registry.register(AgentRuntimeKind.FAKE, factory)
    kit = AgentKit(registry=registry)
    running = asyncio.create_task(kit.run("fake", goal="wait", task_id="cancel-me"))
    await runtime.started.wait()

    receipt = await kit.cancel("fake", "cancel-me")
    repeated = await kit.cancel("fake", "cancel-me")

    assert receipt.disposition is CancellationDisposition.LEGACY_UNCONFIRMED
    assert repeated.disposition is CancellationDisposition.ALREADY_REQUESTED
    assert runtime.cancel_calls == 1
    assert constructions == 1
    with pytest.raises(asyncio.CancelledError):
        await running
    assert runtime.cancelled is True


@pytest.mark.asyncio
async def test_cancel_of_uncached_kind_is_not_active_and_has_no_side_effects() -> None:
    constructions = 0

    def factory() -> FakeAgentRuntime:
        nonlocal constructions
        constructions += 1
        return FakeAgentRuntime()

    registry = RuntimeRegistry()
    registry.register(AgentRuntimeKind.FAKE, factory)
    kit = AgentKit(registry=registry)

    receipt = await kit.cancel("fake", "missing")

    assert receipt.disposition is CancellationDisposition.NOT_ACTIVE
    assert constructions == 0


@pytest.mark.asyncio
async def test_direct_builtin_cancel_returns_receipts_and_emits_interrupted() -> None:
    runtime = BlockingFakeRuntime()
    sink = RecordingEventSink()
    running = asyncio.create_task(
        runtime.run(AgentTask(goal="wait", task_id="direct", event_sink=sink))
    )
    await runtime.started.wait()

    receipt = await runtime.cancel("direct")
    repeated = await runtime.cancel("direct")

    assert isinstance(receipt, CancellationReceipt)
    assert receipt.disposition is CancellationDisposition.REQUESTED
    assert repeated.disposition is CancellationDisposition.ALREADY_REQUESTED
    with pytest.raises(asyncio.CancelledError):
        await running
    assert runtime.cancelled is True
    terminal = [event for event in sink.events if event["name"] == "agent.task.failed"]
    assert len(terminal) == 1
    assert terminal[0]["attributes"]["finish_reason"] == FinishReason.INTERRUPTED


@pytest.mark.asyncio
async def test_queued_deadline_does_not_interrupt_reuse_holder() -> None:
    runtime = SerialFakeRuntime()
    holder = asyncio.create_task(
        runtime.run(AgentTask(goal="holder", task_id="holder"))
    )
    await runtime.holder_started.wait()

    with pytest.raises(AgentTaskTimeoutError):
        await runtime.run(
            AgentTask(
                goal="queued",
                task_id="queued",
                deadline=datetime.now(tz=timezone.utc) + timedelta(milliseconds=10),
            )
        )

    assert runtime.holder_cancelled is False
    assert not holder.done()
    runtime.release_holder.set()
    assert (await holder).output == "holder"


@pytest.mark.asyncio
async def test_external_task_cancellation_emits_one_interrupted_terminal() -> None:
    runtime = BlockingFakeRuntime()
    sink = RecordingEventSink()
    running = asyncio.create_task(
        runtime.run(AgentTask(goal="wait", task_id="external", event_sink=sink))
    )
    await runtime.started.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    terminal = [event for event in sink.events if event["name"] == "agent.task.failed"]
    assert len(terminal) == 1
    assert terminal[0]["attributes"]["finish_reason"] == FinishReason.INTERRUPTED
