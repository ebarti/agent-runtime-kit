from __future__ import annotations

import pytest

from agent_runtime_kit import (
    AgentCapabilities,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    FakeAgentRuntime,
    FinishReason,
    RuntimeNotRegisteredError,
    UnsupportedTaskInputError,
    create_default_registry,
    runtime_kind_value,
)


@pytest.mark.asyncio
async def test_fake_runtime_runs_without_vendor_sdks() -> None:
    runtime = FakeAgentRuntime(output="done")

    result = await runtime.run(AgentTask(goal="test goal"))

    assert result.output == "done"
    assert result.finish_reason == "done"
    assert result.session_id is not None
    assert result.tool_calls[0].tool_name == "fake"


def test_registry_resolves_fake_runtime() -> None:
    registry = create_default_registry()

    runtime = registry.resolve(AgentRuntimeKind.FAKE, output="ok")

    assert isinstance(runtime, FakeAgentRuntime)
    assert registry.capabilities_for("fake").structured_output is True
    assert registry.availability_for("fake").available is True


def test_registry_rejects_missing_runtime() -> None:
    registry = create_default_registry(include_fake=False)

    with pytest.raises(RuntimeNotRegisteredError):
        registry.resolve(AgentRuntimeKind.FAKE)


def test_registry_accepts_namespaced_third_party_kind() -> None:
    # A third party can register a runtime under a namespaced string kind without
    # forking the built-in enum.
    registry = create_default_registry()
    registry.register("x-myorg-agent", lambda **_: FakeAgentRuntime(output="third-party"))

    assert "x-myorg-agent" in registry.kinds()
    runtime = registry.resolve("x-myorg-agent")
    assert isinstance(runtime, FakeAgentRuntime)


def test_agent_task_model_fields_are_keyword_only() -> None:
    from pathlib import Path

    # model/reasoning_effort were inserted mid-dataclass; kw_only keeps the
    # positional layout that predates them, so the fourth positional argument is
    # still working_directory (not model).
    task = AgentTask("goal", "task-1", "system prompt", Path("/tmp"))

    assert task.system == "system prompt"
    assert task.working_directory == Path("/tmp")
    assert task.model is None
    assert task.reasoning_effort is None
    assert AgentTask("g", model="m-1", reasoning_effort="high").model == "m-1"


def test_finish_reason_enum_compares_as_string() -> None:
    result = AgentResult(output="x", finish_reason=FinishReason.FAILED)

    # FinishReason is a str subclass: enum-vs-enum and enum-vs-literal both hold.
    assert result.finish_reason == FinishReason.FAILED
    assert result.finish_reason == "failed"
    assert AgentResult(output="x").finish_reason == FinishReason.DONE
    # And it renders as its value everywhere, on 3.10 and 3.11+ alike — event
    # summaries interpolate finish_reason and must never show "FinishReason.FAILED".
    assert str(FinishReason.FAILED) == "failed"
    assert f"{FinishReason.FAILED}" == "failed"
    assert format(FinishReason.FAILED) == "failed"


def test_coerce_returns_namespaced_string_and_rejects_blank() -> None:
    assert AgentRuntimeKind.coerce("claude-agent-sdk") is AgentRuntimeKind.CLAUDE_AGENT_SDK
    assert AgentRuntimeKind.coerce("x-myorg-agent") == "x-myorg-agent"
    assert runtime_kind_value(AgentRuntimeKind.FAKE) == "fake"
    assert runtime_kind_value("x-myorg-agent") == "x-myorg-agent"
    with pytest.raises(ValueError):
        AgentRuntimeKind.coerce("   ")


@pytest.mark.asyncio
async def test_fake_runtime_satisfies_protocol_with_lifecycle() -> None:
    runtime = FakeAgentRuntime(output="done")

    assert isinstance(runtime, AgentRuntime)
    async with runtime as entered:
        result = await entered.run(AgentTask(goal="x"))
    assert result.output == "done"
    await runtime.aclose()


@pytest.mark.asyncio
async def test_fake_runtime_rejects_unsupported_structured_output() -> None:
    runtime = FakeAgentRuntime(capabilities=AgentCapabilities(structured_output=False))
    task = AgentTask(goal="return json", output_schema={"type": "object"})

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(task)

    assert exc_info.value.field == "output_schema"
