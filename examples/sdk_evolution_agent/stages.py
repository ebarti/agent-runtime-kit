"""Runtime-backed AI stages and implementation gates."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent_runtime_kit import (
    AgentCapabilities,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    PermissionMode,
    PermissionProfile,
    RuntimeAvailability,
    RuntimeRegistry,
)
from agent_runtime_kit.adapters import CodexAgentRuntime, register_adapters
from agent_runtime_kit.events import safe_emit, task_completed_event, task_started_event
from agent_runtime_kit.registry import create_default_registry
from examples.sdk_evolution_agent.models import (
    RUNTIME_CONTRACT_SYMBOLS,
    ApiDiff,
    GateResult,
    RunContext,
)
from examples.sdk_evolution_agent.schemas import (
    ARCHITECTURE_DECISION_SCHEMA,
    DIRECTION_ANALYSIS_SCHEMA,
    IMPLEMENTATION_SUMMARY_SCHEMA,
    REVIEWER_OUTPUT_SCHEMA,
    JsonSchema,
    SchemaValidationError,
    validate_mapping,
)


class StageExecutionError(RuntimeError):
    """Raised when a runtime stage cannot produce valid structured output."""


SDK_EVOLUTION_CODEX_HOME = Path("~/.codex_agent_runtime_sdk").expanduser()


class FixtureEvolutionRuntime:
    """Deterministic fake/test runtime for credential-free local reports."""

    kind = AgentRuntimeKind.FAKE
    capabilities = AgentCapabilities(
        working_directory=True,
        structured_output=True,
        streaming=False,
        tool_audit=False,
        cancellation=False,
    )

    def availability(self) -> RuntimeAvailability:
        """Return an available fake runtime diagnostic."""

        return RuntimeAvailability.ok(self.kind, package="agent-runtime-kit")

    async def run(self, task: AgentTask) -> AgentResult:
        """Return conservative structured outputs for known evolution stages."""

        await safe_emit(task, task_started_event(task, self.kind))
        stage = str(task.metadata.get("stage", "unknown"))
        payload = _fixture_payload(stage, task)
        result = AgentResult(
            output=json.dumps(payload, sort_keys=True),
            parsed_output=payload,
            finish_reason="done",
            session_id=task.task_id,
            rounds=1,
            metadata={"stage": stage, "fixture": True},
        )
        await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> None:
        """Fixture runtime has nothing to cancel."""

        del task_id


def build_registry() -> RuntimeRegistry:
    """Build the runtime registry used by the SDK evolution agent."""

    registry = create_default_registry(include_fake=False)
    registry.register(AgentRuntimeKind.FAKE, FixtureEvolutionRuntime)
    register_adapters(registry)
    registry.register(
        AgentRuntimeKind.CODEX_AGENT_SDK,
        _codex_evolution_runtime,
        replace=True,
    )
    return registry


def _codex_evolution_runtime(**kwargs: Any) -> CodexAgentRuntime:
    SDK_EVOLUTION_CODEX_HOME.mkdir(mode=0o700, parents=True, exist_ok=True)
    SDK_EVOLUTION_CODEX_HOME.chmod(0o700)
    env = dict(kwargs.pop("env", {}) or {})
    env.setdefault("CODEX_HOME", str(SDK_EVOLUTION_CODEX_HOME))
    return CodexAgentRuntime(env=env, **kwargs)


def resolve_runtime(kind: str, *, registry: RuntimeRegistry | None = None) -> AgentRuntime:
    """Resolve a runtime through agent-runtime-kit and verify availability."""

    registry = registry or build_registry()
    runtime = registry.resolve(kind)
    availability = runtime.availability()
    if not availability.available:
        raise StageExecutionError(availability.message)
    return runtime


async def run_stage(
    runtime: AgentRuntime,
    *,
    stage: str,
    payload: Mapping[str, Any],
    schema: JsonSchema,
    context: RunContext,
    write_enabled: bool = False,
) -> dict[str, Any]:
    """Run one AI stage through agent-runtime-kit and validate its output."""

    if not runtime.capabilities.structured_output:
        raise StageExecutionError(f"{runtime.kind.value} cannot honor required output_schema")
    if not runtime.capabilities.working_directory:
        raise StageExecutionError(f"{runtime.kind.value} cannot honor required working_directory")
    permissions = _stage_permissions(runtime, write_enabled=write_enabled)
    task = AgentTask(
        goal=json.dumps(payload, sort_keys=True, default=str),
        system=_stage_system_prompt(stage),
        working_directory=context.workspace,
        permissions=permissions,
        event_sink=context.event_sink,
        output_schema=schema,
        metadata={"stage": stage, "run_id": context.run_id},
    )
    try:
        result = await runtime.run(task)
    except Exception as exc:
        raise StageExecutionError(f"{stage} failed through {runtime.kind.value}: {exc}") from exc
    data = result.parsed_output
    if data is None:
        try:
            data = json.loads(result.output)
        except json.JSONDecodeError as exc:
            raise StageExecutionError(f"{stage} returned no valid structured output") from exc
    try:
        return validate_mapping(data, schema, name=stage)
    except SchemaValidationError as exc:
        raise StageExecutionError(f"{stage} returned invalid structured output: {exc}") from exc


async def run_analysis_pipeline(
    runtime: AgentRuntime,
    *,
    evidence: Mapping[str, Any],
    api_diffs: Sequence[Mapping[str, Any]],
    context: RunContext,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run direction, architecture, and reviewer stages."""

    stage_payload = {"evidence": evidence, "api_diffs": list(api_diffs)}
    direction = await run_stage(
        runtime,
        stage="direction-analysis",
        payload=stage_payload,
        schema=DIRECTION_ANALYSIS_SCHEMA,
        context=context,
    )
    architecture = await run_stage(
        runtime,
        stage="architecture-decision",
        payload={
            "evidence": evidence,
            "api_diffs": list(api_diffs),
            "direction_analysis": direction,
        },
        schema=ARCHITECTURE_DECISION_SCHEMA,
        context=context,
    )
    architecture = with_recursive_impact(architecture, api_diffs)
    review = await run_stage(
        runtime,
        stage="review",
        payload={
            "evidence": evidence,
            "api_diffs": list(api_diffs),
            "direction_analysis": direction,
            "architecture_decision": architecture,
        },
        schema=REVIEWER_OUTPUT_SCHEMA,
        context=context,
    )
    return direction, architecture, review


async def maybe_run_implementation(
    runtime: AgentRuntime,
    *,
    evidence: Mapping[str, Any],
    direction: Mapping[str, Any],
    architecture: Mapping[str, Any],
    review: Mapping[str, Any],
    context: RunContext,
) -> dict[str, Any]:
    """Run implementation only if decision gates permit it."""

    gate = evaluate_implementation_gate(
        architecture,
        review,
        implementation_enabled=context.implementation_enabled,
    )
    if not gate.allowed:
        return {
            "applied": False,
            "changes": [],
            "verification_results": [],
            "blocked_reason": gate.reason,
        }
    return await run_stage(
        runtime,
        stage="implementation",
        payload={
            "evidence": evidence,
            "direction_analysis": direction,
            "architecture_decision": architecture,
            "review": review,
        },
        schema=IMPLEMENTATION_SUMMARY_SCHEMA,
        context=context,
        write_enabled=True,
    )


def evaluate_implementation_gate(
    architecture: Mapping[str, Any],
    review: Mapping[str, Any],
    *,
    implementation_enabled: bool,
) -> GateResult:
    """Decide whether implementation is allowed."""

    if not implementation_enabled:
        return GateResult(False, "report-only mode")
    if architecture.get("manual_design_required"):
        return GateResult(False, "manual_design_required")
    if architecture.get("recursive_self_adaptation_impact") and not architecture.get(
        "self_adaptation_plan"
    ):
        return GateResult(False, "recursive self-adaptation requires a migration plan")
    if str(review.get("status", "")).lower() != "pass":
        return GateResult(False, "reviewer did not pass the proposal")
    if not architecture.get("safe_to_implement"):
        return GateResult(False, "architecture decision is not safe to implement")
    return GateResult(True, "implementation enabled and gates passed")


def detects_recursive_impact(api_diffs: Sequence[Mapping[str, Any] | ApiDiff]) -> bool:
    """Detect whether API diffs touch the agent's own runtime contract."""

    for diff in api_diffs:
        values: list[str] = []
        if isinstance(diff, ApiDiff):
            values.extend(diff.added)
            values.extend(diff.removed)
            values.extend(diff.changed)
        else:
            for key in ("added", "removed", "changed"):
                items = diff.get(key, ())
                if isinstance(items, list | tuple):
                    values.extend(str(item) for item in items)
        if any(symbol in value for symbol in RUNTIME_CONTRACT_SYMBOLS for value in values):
            return True
    return False


def with_recursive_impact(
    architecture: Mapping[str, Any],
    api_diffs: Sequence[Mapping[str, Any] | ApiDiff],
) -> dict[str, Any]:
    """Ensure recursive runtime-contract impacts are explicit."""

    result = dict(architecture)
    if not detects_recursive_impact(api_diffs):
        return result
    result["recursive_self_adaptation_impact"] = True
    result.setdefault(
        "self_adaptation_plan",
        [
            "Update examples/sdk_evolution_agent runtime usage, schemas, tests, and docs "
            "in the same scoped change.",
        ],
    )
    findings = list(result.get("findings") or [])
    findings.append(
        {
            "classification": "manual-design-required",
            "summary": "Runtime contract changes affect the SDK evolution agent itself.",
            "evidence": ["api_diffs"],
        }
    )
    result["findings"] = findings
    return result


def _stage_system_prompt(stage: str) -> str:
    return (
        "You are running inside the local SDK evolution agent. "
        "Use only the provided evidence. Preserve vendor-specific behavior, "
        "state uncertainty explicitly, and never claim implementation occurred "
        "unless it is reflected in the provided artifacts. "
        f"Current stage: {stage}."
    )


def _stage_permissions(runtime: AgentRuntime, *, write_enabled: bool) -> PermissionProfile:
    permissions = PermissionProfile(
        mode=PermissionMode.CAUTIOUS if write_enabled else PermissionMode.STRICT,
        filesystem=(
            FilesystemAccess.WORKSPACE_WRITE if write_enabled else FilesystemAccess.READ_ONLY
        ),
    )
    if write_enabled or runtime.kind is not AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK:
        return permissions
    return PermissionProfile(
        mode=permissions.mode,
        filesystem=permissions.filesystem,
        allowed_tools=("finish",),
    )


def _fixture_payload(stage: str, task: AgentTask) -> dict[str, Any]:
    try:
        source = json.loads(task.goal)
    except json.JSONDecodeError:
        source = {}
    if stage == "direction-analysis":
        packages = [
            {
                "name": package.get("name"),
                "direction": "unknown",
                "evidence": ["deterministic package metadata"],
            }
            for package in source.get("evidence", {}).get("packages", [])
            if isinstance(package, dict)
        ]
        return {
            "packages": packages,
            "themes": [
                {
                    "name": "runtime SDK evolution",
                    "summary": "Fixture runtime records evidence for human or real-runtime review.",
                }
            ],
            "uncertainty": ["fake runtime cannot infer real upstream product direction"],
        }
    if stage == "architecture-decision":
        return {
            "findings": [],
            "safe_to_implement": False,
            "manual_design_required": False,
            "recursive_self_adaptation_impact": detects_recursive_impact(
                source.get("api_diffs", [])
            ),
            "self_adaptation_plan": [],
            "verification_commands": ["uv run pytest tests/test_sdk_evolution_agent.py"],
            "uncertainty": ["fixture decision is conservative"],
        }
    if stage == "review":
        return {
            "status": "pass",
            "reasons": ["fixture review only verifies the pipeline shape"],
            "required_changes": [],
        }
    if stage == "implementation":
        return {
            "applied": False,
            "changes": [],
            "verification_results": [],
            "blocked_reason": "fixture runtime does not edit files",
        }
    return {"status": "unknown", "reasons": [], "required_changes": []}
