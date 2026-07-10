from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import agent_runtime_kit._control as control_module
from agent_runtime_kit import (
    AgentKit,
    AgentResult,
    AgentRuntimeError,
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
from agent_runtime_kit.adapters._common import (
    VendorCleanupQuarantine,
    finish_vendor_cleanup,
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


class GatedCancellationRuntime:
    """Runtime whose cancellation hook can be delayed across run generations."""

    kind = AgentRuntimeKind.FAKE
    capabilities = FakeAgentRuntime().capabilities

    def __init__(self) -> None:
        self._active: dict[str, asyncio.Task[object]] = {}
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release_second = asyncio.Event()
        self.cancel_entered = asyncio.Event()
        self.release_cancel = asyncio.Event()
        self.cancel_calls = 0
        self.second_cancelled = False

    def availability(self) -> RuntimeAvailability:
        return RuntimeAvailability.ok(self.kind)

    async def run(self, task: AgentTask) -> AgentResult:
        current = asyncio.current_task()
        assert current is not None
        self._active[task.task_id] = current
        try:
            return await self._run_task(task)
        finally:
            if self._active.get(task.task_id) is current:
                del self._active[task.task_id]

    async def _run_task(self, task: AgentTask) -> AgentResult:
        if task.goal == "first":
            self.first_started.set()
            await self.release_first.wait()
            return AgentResult(output="first")
        self.second_started.set()
        try:
            await self.release_second.wait()
        except asyncio.CancelledError:
            self.second_cancelled = True
            raise
        return AgentResult(output="second")

    async def cancel(self, task_id: str) -> CancellationReceipt:
        self.cancel_calls += 1
        self.cancel_entered.set()
        await self.release_cancel.wait()
        target = self._active.get(task_id)
        if target is None:
            return CancellationReceipt(
                task_id=task_id,
                kind=self.kind,
                disposition=CancellationDisposition.NOT_ACTIVE,
            )
        target.cancel()
        return CancellationReceipt(
            task_id=task_id,
            kind=self.kind,
            disposition=CancellationDisposition.REQUESTED,
        )

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> GatedCancellationRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()


class BlockingTimeoutSink:
    """Record a timeout terminal, then pause its delivery to expose races."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self.timeout_seen = asyncio.Event()
        self.release_timeout = asyncio.Event()

    async def emit(self, event: object) -> None:
        assert isinstance(event, dict)
        self.events.append(event)
        attributes = event.get("attributes", {})
        if (
            event.get("name") == "agent.task.failed"
            and isinstance(attributes, dict)
            and attributes.get("finish_reason") == FinishReason.TIMED_OUT
        ):
            self.timeout_seen.set()
            await self.release_timeout.wait()


class CleanupBlockingRuntime(FakeAgentRuntime):
    """Expose whether a repeated cancel interrupts operation cleanup."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()
        self.cleanup_interrupted = False

    async def _run_task(self, task: AgentTask) -> AgentResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleanup_started.set()
            try:
                await self.release_cleanup.wait()
            except asyncio.CancelledError:
                self.cleanup_interrupted = True
                raise


class StubbornCleanupRuntime(FakeAgentRuntime):
    """Keep a deadline-cancelled operation alive until a test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.cleanup_started = asyncio.Event()
        self.release_cleanup = asyncio.Event()

    async def _run_task(self, task: AgentTask) -> AgentResult:
        if task.goal == "replacement":
            return AgentResult(output="replacement")
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.cleanup_started.set()
            try:
                await self.release_cleanup.wait()
            except asyncio.CancelledError:
                await self.release_cleanup.wait()


def test_deadline_requires_timezone_and_normalizes_to_utc() -> None:
    with pytest.raises(ValueError, match="must be a datetime"):
        AgentTask(goal="x", deadline="tomorrow")  # type: ignore[arg-type]

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
    assert [event["name"] for event in sink.events] == ["agent.task.failed"]
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

    runtime.started.clear()
    runtime.release.set()
    recovered = await runtime.run(AgentTask(goal="next", task_id="direct"))
    assert recovered.output == "next"


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


@pytest.mark.asyncio
async def test_delayed_cancel_hook_cannot_target_later_same_id_run() -> None:
    runtime = GatedCancellationRuntime()
    kit = AgentKit(register_default_adapters=False)
    first = asyncio.create_task(kit.run(runtime, goal="first", task_id="same"))
    await runtime.first_started.wait()

    cancelling = asyncio.create_task(kit.cancel(runtime, "same"))
    await runtime.cancel_entered.wait()
    with pytest.raises(asyncio.CancelledError):
        await first

    # The completed generation remains reserved until its delayed task-id hook
    # settles, so no replacement can become the hook's accidental target.
    with pytest.raises(ValueError, match="already active"):
        await kit.run(runtime, goal="second", task_id="same")

    runtime.release_cancel.set()
    receipt = await cancelling
    assert receipt.disposition is CancellationDisposition.REQUESTED

    second = asyncio.create_task(kit.run(runtime, goal="second", task_id="same"))
    await runtime.second_started.wait()
    assert runtime.second_cancelled is False
    runtime.release_second.set()
    assert (await second).output == "second"


@pytest.mark.asyncio
async def test_cancelling_cancel_request_still_cancels_target_and_releases_generation() -> None:
    runtime = GatedCancellationRuntime()
    kit = AgentKit(register_default_adapters=False)
    running = asyncio.create_task(kit.run(runtime, goal="first", task_id="same"))
    await runtime.first_started.wait()

    cancelling = asyncio.create_task(kit.cancel(runtime, "same"))
    await runtime.cancel_entered.wait()
    cancelling.cancel()

    with pytest.raises(asyncio.CancelledError):
        await cancelling
    with pytest.raises(asyncio.CancelledError):
        await running

    replacement = asyncio.create_task(kit.run(runtime, goal="second", task_id="same"))
    await runtime.second_started.wait()
    runtime.release_second.set()
    assert (await replacement).output == "second"


@pytest.mark.asyncio
async def test_timeout_racing_external_cancel_emits_only_timed_out_terminal() -> None:
    runtime = BlockingFakeRuntime()
    sink = BlockingTimeoutSink()
    running = asyncio.create_task(
        runtime.run(
            AgentTask(
                goal="wait",
                task_id="timeout-race",
                deadline=datetime.now(tz=timezone.utc) + timedelta(milliseconds=10),
                event_sink=sink,  # type: ignore[arg-type]
            )
        )
    )
    await sink.timeout_seen.wait()

    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running

    terminals = [
        event
        for event in sink.events
        if isinstance(event, dict) and event.get("name") == "agent.task.failed"
    ]
    assert len(terminals) == 1
    assert terminals[0]["attributes"]["finish_reason"] == FinishReason.TIMED_OUT


@pytest.mark.asyncio
async def test_timeout_wins_over_external_cancel_during_operation_cleanup() -> None:
    runtime = CleanupBlockingRuntime()
    sink = RecordingEventSink()
    running = asyncio.create_task(
        runtime.run(
            AgentTask(
                goal="wait",
                task_id="cleanup-timeout-race",
                deadline=datetime.now(tz=timezone.utc) + timedelta(milliseconds=10),
                event_sink=sink,
            )
        )
    )
    await runtime.cleanup_started.wait()

    running.cancel()
    await asyncio.sleep(0)
    runtime.release_cleanup.set()

    with pytest.raises(AgentTaskTimeoutError):
        await running
    terminal = [event for event in sink.events if event["name"] == "agent.task.failed"]
    assert len(terminal) == 1
    assert terminal[0]["attributes"]["finish_reason"] == FinishReason.TIMED_OUT


@pytest.mark.asyncio
async def test_repeated_runtime_cancel_does_not_interrupt_operation_cleanup() -> None:
    runtime = CleanupBlockingRuntime()
    running = asyncio.create_task(
        runtime.run(AgentTask(goal="wait", task_id="cleanup"))
    )
    await runtime.started.wait()

    first = await runtime.cancel("cleanup")
    await runtime.cleanup_started.wait()
    repeated = await runtime.cancel("cleanup")

    assert first.disposition is CancellationDisposition.REQUESTED
    assert repeated.disposition is CancellationDisposition.ALREADY_REQUESTED
    assert runtime.cleanup_interrupted is False
    runtime.release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert runtime.cleanup_interrupted is False


@pytest.mark.asyncio
async def test_vendor_cleanup_helper_survives_repeated_task_cancellation() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = False

    async def cleanup() -> None:
        nonlocal cleanup_finished
        cleanup_started.set()
        await release_cleanup.wait()
        cleanup_finished = True

    async def owner() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            assert await finish_vendor_cleanup(cleanup()) is None
            raise

    running = asyncio.create_task(owner())
    await asyncio.sleep(0)
    running.cancel()
    await cleanup_started.wait()
    running.cancel()
    await asyncio.sleep(0)

    assert cleanup_finished is False
    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert cleanup_finished is True


@pytest.mark.asyncio
async def test_vendor_cleanup_helper_has_bounded_liveness() -> None:
    cleanup_started = asyncio.Event()
    cleanup_cancelled = asyncio.Event()
    release_cleanup = asyncio.Event()

    async def wedged_cleanup() -> None:
        cleanup_started.set()
        try:
            await release_cleanup.wait()
        except asyncio.CancelledError:
            cleanup_cancelled.set()
            await release_cleanup.wait()

    outcome = await finish_vendor_cleanup(wedged_cleanup(), timeout=0.01)
    await asyncio.sleep(0)

    assert cleanup_started.is_set()
    assert cleanup_cancelled.is_set()
    assert isinstance(outcome, TimeoutError)
    quarantine = VendorCleanupQuarantine()
    quarantine.track(outcome)
    with pytest.raises(AgentRuntimeError, match="cleanup still pending"):
        quarantine.ensure_ready(AgentRuntimeKind.FAKE)
    release_cleanup.set()
    await asyncio.sleep(0)
    quarantine.ensure_ready(AgentRuntimeKind.FAKE)


@pytest.mark.asyncio
async def test_detached_operation_quarantines_runtime_until_cleanup_settles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(control_module, "_OPERATION_CLEANUP_TIMEOUT", 0.01)
    runtime = StubbornCleanupRuntime()

    with pytest.raises(AgentTaskTimeoutError):
        await runtime.run(
            AgentTask(
                goal="stubborn",
                task_id="old",
                deadline=datetime.now(tz=timezone.utc) + timedelta(milliseconds=10),
            )
        )

    assert runtime.cleanup_started.is_set()
    with pytest.raises(AgentRuntimeError, match="still cleaning up"):
        await runtime.run(AgentTask(goal="replacement", task_id="new"))

    runtime.release_cleanup.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert (
        await runtime.run(AgentTask(goal="replacement", task_id="new"))
    ).output == "replacement"


@pytest.mark.asyncio
async def test_cancel_after_completed_run_is_truthfully_not_active() -> None:
    runtime = LegacyBlockingRuntime()
    runtime.release.set()
    kit = AgentKit(register_default_adapters=False)

    assert (await kit.run(runtime, goal="done", task_id="finished")).output == "done"
    receipt = await kit.cancel(runtime, "finished")

    assert receipt.disposition is CancellationDisposition.NOT_ACTIVE
    assert runtime.cancel_calls == 0
