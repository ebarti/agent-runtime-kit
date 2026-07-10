"""Cross-adapter conformance: every built-in adapter honors the AgentRuntime contract.

This enforces uniformity that was previously only checked ad hoc per adapter, so a
new or refactored adapter cannot silently diverge from the shared protocol.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from pathlib import Path

import pytest
from test_antigravity_adapter import make_runtime as make_antigravity_runtime
from test_claude_adapter import FakeClaudeOptions, assistant, make_query, result_message
from test_codex_adapter import make_runtime as make_codex_runtime

from agent_runtime_kit import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    RuntimeAvailability,
    RuntimeReadinessProvider,
)
from agent_runtime_kit._types import AgentRuntime
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)
from agent_runtime_kit.testing import RecordingEventSink


def _build_claude(_tmp_path: Path) -> ClaudeAgentRuntime:
    return ClaudeAgentRuntime(
        query_func=make_query([assistant("done"), result_message()]),
        options_cls=FakeClaudeOptions,
    )


def _build_codex(_tmp_path: Path) -> CodexAgentRuntime:
    return make_codex_runtime()


def _build_antigravity(tmp_path: Path) -> AntigravityAgentRuntime:
    return make_antigravity_runtime(data_dir=tmp_path)


# (kind, builder) for every built-in adapter; new adapters should be added here.
BUILDERS: list[tuple[AgentRuntimeKind, Callable[[Path], AgentRuntime]]] = [
    (AgentRuntimeKind.CLAUDE_AGENT_SDK, _build_claude),
    (AgentRuntimeKind.CODEX_AGENT_SDK, _build_codex),
    (AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK, _build_antigravity),
]


@pytest.mark.parametrize("kind,builder", BUILDERS, ids=lambda v: getattr(v, "value", ""))
def test_adapter_satisfies_runtime_protocol(
    kind: AgentRuntimeKind, builder: Callable[[Path], AgentRuntime], tmp_path: Path
) -> None:
    runtime = builder(tmp_path)

    assert isinstance(runtime, AgentRuntime)
    assert runtime.kind is kind
    assert isinstance(runtime.capabilities, AgentCapabilities)


@pytest.mark.parametrize("kind,builder", BUILDERS, ids=lambda v: getattr(v, "value", ""))
def test_adapter_exposes_async_lifecycle(
    kind: AgentRuntimeKind, builder: Callable[[Path], AgentRuntime], tmp_path: Path
) -> None:
    runtime = builder(tmp_path)

    for name in ("run", "cancel", "aclose", "__aenter__", "__aexit__"):
        method = getattr(runtime, name)
        assert inspect.iscoroutinefunction(method), f"{kind.value}.{name} must be async"


@pytest.mark.parametrize("kind,builder", BUILDERS, ids=lambda v: getattr(v, "value", ""))
def test_adapter_exposes_optional_async_readiness_probe(
    kind: AgentRuntimeKind, builder: Callable[[Path], AgentRuntime], tmp_path: Path
) -> None:
    runtime = builder(tmp_path)

    assert isinstance(runtime, RuntimeReadinessProvider)
    assert inspect.iscoroutinefunction(runtime.check_readiness), (
        f"{kind.value}.check_readiness must be async"
    )


@pytest.mark.parametrize("kind,builder", BUILDERS, ids=lambda v: getattr(v, "value", ""))
def test_adapter_availability_returns_diagnostic(
    kind: AgentRuntimeKind, builder: Callable[[Path], AgentRuntime], tmp_path: Path
) -> None:
    diagnostic = builder(tmp_path).availability()

    assert isinstance(diagnostic, RuntimeAvailability)
    assert diagnostic.kind is kind


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,builder", BUILDERS, ids=lambda v: getattr(v, "value", ""))
async def test_adapter_run_emits_ordered_lifecycle_events(
    kind: AgentRuntimeKind, builder: Callable[[Path], AgentRuntime], tmp_path: Path
) -> None:
    sink = RecordingEventSink()

    async with builder(tmp_path) as runtime:
        result = await runtime.run(AgentTask(goal="conformance", event_sink=sink))

    assert isinstance(result, AgentResult)
    names = [event["name"] for event in sink.events]
    assert names[0] == "agent.task.started"
    assert names[-1] == "agent.task.completed"
