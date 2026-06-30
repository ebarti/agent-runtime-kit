"""Runtime-backed AI stages and implementation gates."""

from __future__ import annotations

import json
import re
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
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
    register_adapters,
)
from agent_runtime_kit.events import safe_emit, task_completed_event, task_started_event
from agent_runtime_kit.registry import create_default_registry
from examples.sdk_evolution_agent.auth import prepare_isolated_codex_home
from examples.sdk_evolution_agent.models import (
    RUNTIME_CONTRACT_SYMBOLS,
    ApiDiff,
    GateResult,
    RunContext,
)
from examples.sdk_evolution_agent.schemas import (
    ARCHITECTURE_DECISION_SCHEMA,
    DIRECTION_ANALYSIS_SCHEMA,
    REVIEWER_OUTPUT_SCHEMA,
    JsonSchema,
    SchemaValidationError,
    validate_mapping,
)


class StageExecutionError(RuntimeError):
    """Raised when a runtime stage cannot produce valid structured output."""


SDK_EVOLUTION_CODEX_HOME = Path("~/.codex_agent_runtime_sdk").expanduser()
SDK_EVOLUTION_CODEX_MODEL = "gpt-5.5"
SDK_EVOLUTION_CODEX_REASONING_EFFORT = "xhigh"


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
    registry.register(
        AgentRuntimeKind.CLAUDE_AGENT_SDK,
        _claude_evolution_runtime,
        replace=True,
    )
    registry.register(
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
        _antigravity_evolution_runtime,
        replace=True,
    )
    return registry


def _codex_evolution_runtime(**kwargs: Any) -> CodexAgentRuntime:
    codex_home = prepare_isolated_codex_home(codex_home=SDK_EVOLUTION_CODEX_HOME)
    env = dict(kwargs.pop("env", {}) or {})
    env.setdefault("CODEX_HOME", str(codex_home))
    kwargs.setdefault("default_model", SDK_EVOLUTION_CODEX_MODEL)
    kwargs.setdefault("reuse_process", True)
    return CodexAgentRuntime(env=env, **kwargs)


def _claude_evolution_runtime(**kwargs: Any) -> ClaudeAgentRuntime:
    kwargs.setdefault("reuse_process", True)
    return ClaudeAgentRuntime(**kwargs)


def _antigravity_evolution_runtime(**kwargs: Any) -> AntigravityAgentRuntime:
    kwargs.setdefault("reuse_process", True)
    return AntigravityAgentRuntime(**kwargs)


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
        system=_stage_system_prompt(stage, schema),
        working_directory=context.workspace,
        permissions=permissions,
        event_sink=context.event_sink,
        output_schema=schema,
        metadata=_stage_metadata(runtime, stage=stage, context=context),
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
    release_notes: Sequence[Mapping[str, Any]],
    behavior: Mapping[str, Any],
    context: RunContext,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Run direction, architecture, and reviewer stages."""

    stage_payload = {
        "evidence": evidence,
        "api_diffs": list(api_diffs),
        "release_notes": list(release_notes),
        "behavior": behavior,
    }
    direction = await run_stage(
        runtime,
        stage="direction-analysis",
        payload=stage_payload,
        schema=DIRECTION_ANALYSIS_SCHEMA,
        context=context,
    )
    direction = _compact_stage_output(direction)
    architecture = await run_stage(
        runtime,
        stage="architecture-decision",
        payload={
            "evidence": evidence,
            "api_diffs": list(api_diffs),
            "release_notes": list(release_notes),
            "behavior": behavior,
            "direction_analysis": direction,
        },
        schema=ARCHITECTURE_DECISION_SCHEMA,
        context=context,
    )
    architecture = with_recursive_impact(architecture, api_diffs)
    architecture = with_candidate_api_diff_guard(architecture, evidence, api_diffs)
    architecture = with_release_note_guard(architecture, release_notes)
    architecture = with_behavior_probe_guard(architecture, behavior)
    architecture = with_manual_design_gate(architecture)
    architecture = _compact_stage_output(architecture)
    review = await run_stage(
        runtime,
        stage="review",
        payload={
            "evidence": evidence,
            "api_diffs": list(api_diffs),
            "release_notes": list(release_notes),
            "behavior": behavior,
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
            "allowed": False,
            "changes": [],
            "verification_results": [],
            "blocked_reason": gate.reason,
        }
    del runtime, evidence, direction, review
    return {
        "applied": False,
        "allowed": True,
        "changes": [],
        "verification_results": [],
        "blocked_reason": "",
        "planned_changes": list(architecture.get("self_adaptation_plan") or []),
    }


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
    if not _review_passed(review):
        return GateResult(False, "reviewer did not pass the proposal")
    if not architecture.get("safe_to_implement"):
        return GateResult(False, "architecture decision is not safe to implement")
    return GateResult(True, "implementation enabled and gates passed")


def _review_passed(review: Mapping[str, Any]) -> bool:
    status = str(review.get("status", "")).strip().lower()
    return status in {"pass", "passed", "approve", "approved", "accepted"}


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


def with_candidate_api_diff_guard(
    architecture: Mapping[str, Any],
    evidence: Mapping[str, Any],
    api_diffs: Sequence[Mapping[str, Any] | ApiDiff],
) -> dict[str, Any]:
    """Block SDK update implementation when candidate API evidence is missing."""

    update_packages = _refresh_update_packages(evidence)
    if not update_packages:
        return dict(architecture)
    diff_packages = {
        diff.package if isinstance(diff, ApiDiff) else str(diff.get("package") or "")
        for diff in api_diffs
    }
    missing = tuple(sorted(package for package in update_packages if package not in diff_packages))
    if not missing:
        return dict(architecture)

    result = dict(architecture)
    result["safe_to_implement"] = False
    result["manual_design_required"] = True
    findings = list(result.get("findings") or [])
    findings.append(
        {
            "classification": "manual-design-required",
            "summary": (
                "SDK update candidates require candidate-version API snapshot diffs "
                "before implementation can be considered safe."
            ),
            "evidence": [f"missing api_diffs for {package}" for package in missing],
        }
    )
    result["findings"] = findings
    uncertainty = list(result.get("uncertainty") or [])
    uncertainty.append(
        "Candidate API diffs were not available for update candidate(s): "
        + ", ".join(missing)
    )
    result["uncertainty"] = uncertainty
    plan = list(result.get("self_adaptation_plan") or [])
    plan.append(
        "Rerun with candidate API inspection and review the generated api_diffs before "
        "changing adapters or dependency locks."
    )
    result["self_adaptation_plan"] = plan
    return result


def with_release_note_guard(
    architecture: Mapping[str, Any],
    release_notes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Block implementation when release-note collection itself failed."""

    failed = [
        str(item.get("package"))
        for item in release_notes
        if item.get("to_version") and item.get("status") == "unavailable"
    ]
    if not failed:
        return dict(architecture)
    result = dict(architecture)
    result["safe_to_implement"] = False
    result["manual_design_required"] = True
    findings = list(result.get("findings") or [])
    findings.append(
        {
            "classification": "manual-design-required",
            "summary": "Release-note evidence could not be collected for update candidates.",
            "evidence": [f"release notes unavailable for {package}" for package in failed],
        }
    )
    result["findings"] = findings
    uncertainty = list(result.get("uncertainty") or [])
    uncertainty.append("Missing release-note evidence for: " + ", ".join(sorted(failed)))
    result["uncertainty"] = uncertainty
    return result


def with_behavior_probe_guard(
    architecture: Mapping[str, Any],
    behavior: Mapping[str, Any],
) -> dict[str, Any]:
    """Block implementation when candidate behavior probes fail."""

    diffs = behavior.get("diffs")
    if not isinstance(diffs, list):
        return dict(architecture)
    breaking = [
        diff
        for diff in diffs
        if isinstance(diff, Mapping) and str(diff.get("severity")) == "breaking"
    ]
    if not breaking:
        return dict(architecture)
    result = dict(architecture)
    result["safe_to_implement"] = False
    result["manual_design_required"] = True
    findings = list(result.get("findings") or [])
    findings.append(
        {
            "classification": "manual-design-required",
            "summary": "Candidate SDK behavior probes detected breaking adapter-contract drift.",
            "evidence": [
                f"{diff.get('package')}:{diff.get('probe')} {diff.get('summary')}"
                for diff in breaking
            ],
        }
    )
    result["findings"] = findings
    uncertainty = list(result.get("uncertainty") or [])
    uncertainty.append("Breaking behavior probes require manual adapter design review.")
    result["uncertainty"] = uncertainty
    return result


def with_manual_design_gate(architecture: Mapping[str, Any]) -> dict[str, Any]:
    """Make manual design decisions block implementation unambiguously."""

    result = dict(architecture)
    if result.get("manual_design_required"):
        result["safe_to_implement"] = False
    return result


def _refresh_update_packages(evidence: Mapping[str, Any]) -> tuple[str, ...]:
    preview = evidence.get("refresh_preview")
    if not isinstance(preview, Mapping):
        return ()
    text = f"{preview.get('stdout') or ''}\n{preview.get('stderr') or ''}"
    return tuple(
        sorted(set(re.findall(r"Update\s+([A-Za-z0-9_.-]+)\s+v\S+\s+->\s+v\S+", text)))
    )


def _compact_stage_output(value: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _compact_stage_value(item) for key, item in value.items()}


def _compact_stage_value(value: Any, *, string_limit: int = 800, list_limit: int = 8) -> Any:
    if isinstance(value, str):
        if len(value) <= string_limit:
            return value
        return value[: string_limit - 16].rstrip() + " [truncated]"
    if isinstance(value, list):
        return [
            _compact_stage_value(item, string_limit=string_limit, list_limit=list_limit)
            for item in value[:list_limit]
        ]
    if isinstance(value, dict):
        return {
            key: _compact_stage_value(item, string_limit=string_limit, list_limit=list_limit)
            for key, item in value.items()
        }
    return value


def _stage_system_prompt(stage: str, schema: JsonSchema) -> str:
    prompt = (
        "You are running inside the local SDK evolution agent. "
        "Use only the provided evidence. Preserve vendor-specific behavior, "
        "state uncertainty explicitly, and never claim implementation occurred "
        "unless it is reflected in the provided artifacts. "
        "Return only one JSON object that validates against the provided schema. "
        "Do not include Markdown, code fences, file links, or prose outside JSON. "
        "Do not call shell, command, file, or workspace tools; the deterministic "
        "evidence bundle already contains the inspected data. "
        "Keep each array to at most five high-signal items and each string concise. "
        f"Current stage: {stage}. "
        f"Output schema: {json.dumps(schema, sort_keys=True)}"
    )
    if stage in {"architecture-decision", "review"}:
        prompt += (
            " Deterministic gate policy: candidate API diffs prove API shape drift, "
            "while behavior_diffs prove whether the adapter contract still holds. "
            "For adapter-contract probes, severity none means the required adapter "
            "contract is compatible even when probe details or public API snapshots "
            "show optional field churn. "
            "Do not mark manual_design_required, unsafe, or review rejection solely "
            "because public top-level symbols were added or removed when behavior "
            "probes pass before and after and there is no adapter-source evidence "
            "that the removed symbols are used. Breaking behavior_diffs, missing "
            "candidate API diffs, unavailable required release-note evidence, "
            "reviewer-identified unsupported vendor behavior, or recursive "
            "runtime-contract impact remain hard blockers. Release-note status found "
            "is direct release-note evidence. Status no-matching-version is source "
            "coverage with explicit uncertainty, not unavailable evidence."
        )
    if stage == "review":
        prompt += " The review status must be exactly pass or reject."
    return prompt


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


def _stage_metadata(
    runtime: AgentRuntime,
    *,
    stage: str,
    context: RunContext,
) -> dict[str, Any]:
    metadata: dict[str, Any] = {"stage": stage, "run_id": context.run_id}
    if runtime.kind is AgentRuntimeKind.CODEX_AGENT_SDK:
        metadata["model"] = SDK_EVOLUTION_CODEX_MODEL
        metadata["reasoning_effort"] = SDK_EVOLUTION_CODEX_REASONING_EFFORT
    return metadata


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
