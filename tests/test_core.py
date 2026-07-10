from __future__ import annotations

import copy
import json
import pickle
from dataclasses import asdict

import pytest

from agent_runtime_kit import (
    AgentCapabilities,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    ArtifactRef,
    FakeAgentRuntime,
    FilesystemAccess,
    FinishReason,
    McpServerConfig,
    OutputSchemaError,
    PermissionMode,
    PermissionProfile,
    RuntimeAvailability,
    RuntimeNotRegisteredError,
    ToolCallAudit,
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


def test_permission_profile_coerces_string_literals_to_enum_members() -> None:
    profile = PermissionProfile(mode="strict", filesystem="read-only")  # type: ignore[arg-type]

    # Identity, not just equality: adapters gate on `mode is PermissionMode.STRICT`,
    # so coercion must produce the actual members or the posture silently loosens.
    assert profile.mode is PermissionMode.STRICT
    assert profile.filesystem is FilesystemAccess.READ_ONLY
    # Enum members pass through untouched.
    assert PermissionProfile(mode=PermissionMode.CAUTIOUS).mode is PermissionMode.CAUTIOUS


def test_permission_profile_rejects_unknown_literals() -> None:
    with pytest.raises(ValueError) as exc_info:
        PermissionProfile(mode="paranoid")  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert "paranoid" in message
    # The error teaches the valid vocabulary.
    assert "strict" in message and "permissive" in message

    with pytest.raises(ValueError):
        PermissionProfile(filesystem="ro")  # type: ignore[arg-type]


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


def test_agent_task_does_not_leak_caller_mapping() -> None:
    source = {"model": "x"}
    task = AgentTask(goal="g", metadata=source)

    # Mutating the caller's dict must not mutate the frozen task, and the field
    # itself must be read-only.
    source["model"] = "mutated"
    assert task.metadata["model"] == "x"
    with pytest.raises(TypeError):
        task.metadata["model"] = "nope"  # type: ignore[index]


def test_agent_task_rejects_invalid_output_schema_at_construction() -> None:
    with pytest.raises(OutputSchemaError, match="invalid output_schema"):
        AgentTask(goal="g", output_schema={"required": "not-an-array"})


def test_agent_result_distinguishes_absent_output_from_valid_null() -> None:
    absent = AgentResult(output="")
    inferred = AgentResult(output="", parsed_output={"ok": True})
    valid_null = AgentResult(output="null", parsed_output=None, parsed_output_available=True)

    assert absent.parsed_output_available is False
    assert inferred.parsed_output_available is True
    assert valid_null.parsed_output is None
    assert valid_null.parsed_output_available is True


@pytest.mark.parametrize(
    ("instance", "field_name"),
    [
        (AgentTask(goal="g", metadata={"k": "v"}), "metadata"),
        (AgentResult(output="ok", metadata={"k": "v"}), "metadata"),
        (AgentTask(goal="g", output_schema={"k": "v"}), "output_schema"),
        (RuntimeAvailability(kind="fake", available=True, metadata={"k": "v"}), "metadata"),
        (McpServerConfig(name="n", command="c", env={"k": "v"}), "env"),
        (ToolCallAudit(tool_name="t", arguments={"k": "v"}), "arguments"),
        (ArtifactRef(uri="u", metadata={"k": "v"}), "metadata"),
    ],
    ids=[
        "AgentTask.metadata",
        "AgentResult.metadata",
        "AgentTask.output_schema",
        "RuntimeAvailability.metadata",
        "McpServerConfig.env",
        "ToolCallAudit.arguments",
        "ArtifactRef.metadata",
    ],
)
def test_frozen_models_stay_serializable(instance: object, field_name: str) -> None:
    # Every wrapped mapping field, on every frozen model: freezing must not break
    # the standard object plumbing embedding applications rely on (asdict for
    # logging, pickle for multiprocessing, deepcopy, json), and the read-only
    # behavior must survive each of those round-trips' construction paths.
    mapping = getattr(instance, field_name)

    assert asdict(instance)[field_name] == {"k": "v"}  # type: ignore[call-overload]
    assert getattr(copy.deepcopy(instance), field_name) == {"k": "v"}
    assert json.dumps(mapping) == '{"k": "v"}'

    restored = pickle.loads(pickle.dumps(instance))
    assert getattr(restored, field_name) == {"k": "v"}
    with pytest.raises(TypeError):
        mapping["k"] = "w"
    with pytest.raises(TypeError):
        getattr(restored, field_name)["k"] = "w"  # a pickle round-trip stays read-only


def test_frozen_mapping_is_shallow() -> None:
    # The freeze is intentionally shallow: only the top-level mapping rejects
    # writes, while nested containers keep their original types and mutability
    # (deep-freezing would surprise asdict/deepcopy/json consumers). Docs and
    # CHANGELOG scope their read-only claims to the top level; if this ever
    # becomes a deep freeze, update those claims together with this test.
    task = AgentTask(goal="g", metadata={"nested": {"a": 1}})

    with pytest.raises(TypeError):
        task.metadata["nested"] = {}  # type: ignore[index]
    task.metadata["nested"]["a"] = 2
    assert task.metadata["nested"]["a"] == 2


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


@pytest.mark.asyncio
async def test_fake_runtime_fails_when_generated_output_does_not_match_schema() -> None:
    runtime = FakeAgentRuntime(output="done")
    task = AgentTask(
        goal="return count",
        output_schema={
            "type": "object",
            "properties": {"count": {"type": "integer"}},
            "required": ["count"],
        },
    )

    result = await runtime.run(task)

    assert result.finish_reason == "failed"
    assert "does not conform" in (result.error or "")
    assert result.parsed_output_available is False
