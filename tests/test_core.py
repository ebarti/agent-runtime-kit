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
    SessionResumeState,
    ToolCallAudit,
    UnsupportedTaskInputError,
    Usage,
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


@pytest.mark.parametrize("goal", ["", "   "])
def test_agent_task_rejects_blank_goal(goal: str) -> None:
    with pytest.raises(ValueError, match="AgentTask.goal"):
        AgentTask(goal=goal)


@pytest.mark.parametrize("sdk_executions", [0, -1, True])
def test_agent_task_requires_positive_sdk_execution_hint(sdk_executions: object) -> None:
    with pytest.raises(ValueError, match="sdk_executions"):
        AgentTask(goal="g", sdk_executions=sdk_executions)  # type: ignore[arg-type]


@pytest.mark.parametrize("budget", [-1.0, float("inf"), float("nan"), True])
def test_agent_task_rejects_invalid_budget(budget: object) -> None:
    with pytest.raises(ValueError, match="budget_usd"):
        AgentTask(goal="g", budget_usd=budget)  # type: ignore[arg-type]


def test_agent_task_rejects_ambiguous_session_and_duplicate_mcp_names() -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        AgentTask(
            goal="g",
            session_id="session-1",
            resume_from=SessionResumeState(session_id="session-1"),
        )

    duplicate_servers = (
        McpServerConfig(name="repo", command="first"),
        McpServerConfig(name="repo", command="second"),
    )
    with pytest.raises(ValueError, match="duplicate names"):
        AgentTask(goal="g", mcp_servers=duplicate_servers)


def test_value_objects_reject_blank_or_conflicting_identity() -> None:
    with pytest.raises(ValueError, match="McpServerConfig.name"):
        McpServerConfig(name=" ", command="mcp")
    with pytest.raises(ValueError, match="McpServerConfig.command"):
        McpServerConfig(name="repo", command="")
    with pytest.raises(ValueError, match="SessionResumeState.session_id"):
        SessionResumeState(session_id=" ")
    with pytest.raises(ValueError, match="both allow and disallow"):
        PermissionProfile(allowed_tools=("Read",), disallowed_tools=("Read",))
    with pytest.raises(ValueError, match="duplicates"):
        PermissionProfile(allowed_tools=("Read", "Read"))
    with pytest.raises(ValueError, match="network"):
        PermissionProfile(network="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="scalar string"):
        PermissionProfile(allowed_tools="Read")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="scalar string"):
        McpServerConfig(name="repo", command="mcp", args="--root")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="args must contain only strings"):
        McpServerConfig(name="repo", command="mcp", args=(1,))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="env values"):
        McpServerConfig(name="repo", command="mcp", env={"PORT": 3})  # type: ignore[dict-item]
    with pytest.raises(ValueError, match="scalar string"):
        SessionResumeState(session_id="s", transcript="text")  # type: ignore[arg-type]


def test_sequence_value_fields_are_canonicalized_to_tuples() -> None:
    profile = PermissionProfile(allowed_tools=["Read"])  # type: ignore[arg-type]
    server = McpServerConfig(name="repo", command="mcp", args=["serve"])  # type: ignore[arg-type]
    resume = SessionResumeState(session_id="s", transcript=[{"x": 1}])  # type: ignore[arg-type]

    assert profile.allowed_tools == ("Read",)
    assert server.args == ("serve",)
    assert resume.transcript == ({"x": 1},)


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


def test_agent_result_success_is_explicit_and_rounds_are_non_negative() -> None:
    assert AgentResult(output="ok").is_success is True
    assert AgentResult(output="", finish_reason="failed").is_success is False
    assert (
        AgentResult(output="", finish_reason="failed", error="vendor failed").is_success is False
    )

    with pytest.raises(ValueError, match="rounds"):
        AgentResult(output="", rounds=-1)
    with pytest.raises(ValueError, match="AgentResult.error"):
        AgentResult(output="", finish_reason="failed", error=" ")
    with pytest.raises(ValueError, match="non-success"):
        AgentResult(output="", error="contradictory")


def test_usage_distinguishes_unknown_from_reported_zero_and_rejects_negative_values() -> None:
    unknown = Usage()
    reported_zero = Usage(
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        total_tokens=0,
        cost_usd=0.0,
    )

    assert unknown.input_tokens is None
    assert unknown.cost_usd is None
    assert reported_zero.input_tokens == 0
    assert reported_zero.cost_usd == 0.0
    with pytest.raises(ValueError, match="input_tokens"):
        Usage(input_tokens=-1)
    with pytest.raises(ValueError, match="cost_usd"):
        Usage(cost_usd=float("inf"))


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
