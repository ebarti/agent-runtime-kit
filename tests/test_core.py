from __future__ import annotations

import pytest

from agent_runtime_kit import (
    AgentCapabilities,
    AgentRuntimeKind,
    AgentTask,
    FakeAgentRuntime,
    RuntimeNotRegisteredError,
    UnsupportedTaskInputError,
    create_default_registry,
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


@pytest.mark.asyncio
async def test_fake_runtime_rejects_unsupported_structured_output() -> None:
    runtime = FakeAgentRuntime(capabilities=AgentCapabilities(structured_output=False))
    task = AgentTask(goal="return json", output_schema={"type": "object"})

    with pytest.raises(UnsupportedTaskInputError) as exc_info:
        await runtime.run(task)

    assert exc_info.value.field == "output_schema"
