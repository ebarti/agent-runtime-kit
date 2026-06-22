from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime_kit import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    RuntimeAvailability,
)
from agent_runtime_kit.adapters import CodexAgentRuntime
from examples.sdk_evolution_agent.cli import RunOptions, parse_args, run_agent
from examples.sdk_evolution_agent.collectors import (
    build_refresh_preview_command,
    collect_evidence,
    cutoff_free_env,
    run_refresh_preview,
)
from examples.sdk_evolution_agent.models import CommandResult, RunContext
from examples.sdk_evolution_agent.pr import build_draft_pr_body
from examples.sdk_evolution_agent.schemas import (
    DIRECTION_ANALYSIS_SCHEMA,
    SchemaValidationError,
    validate_mapping,
)
from examples.sdk_evolution_agent.snapshots import diff_snapshots, snapshot_current_api
from examples.sdk_evolution_agent.stages import (
    SDK_EVOLUTION_CODEX_HOME,
    FixtureEvolutionRuntime,
    StageExecutionError,
    build_registry,
    detects_recursive_impact,
    evaluate_implementation_gate,
    run_stage,
    with_recursive_impact,
)


def test_cutoff_free_env_removes_uv_freshness_cutoffs() -> None:
    env, removed = cutoff_free_env(
        {
            "UV_EXCLUDE_NEWER": "2026-01-01",
            "UV_EXCLUDE_NEWER_PACKAGE_CLAUDE_AGENT_SDK": "2026-01-01",
            "CUSTOM_EXCLUDE_NEWER": "2026-01-01",
            "KEEP": "1",
        }
    )

    assert "UV_EXCLUDE_NEWER" not in env
    assert "UV_EXCLUDE_NEWER_PACKAGE_CLAUDE_AGENT_SDK" not in env
    assert "CUSTOM_EXCLUDE_NEWER" not in env
    assert env["KEEP"] == "1"
    assert removed == (
        "CUSTOM_EXCLUDE_NEWER",
        "UV_EXCLUDE_NEWER",
        "UV_EXCLUDE_NEWER_PACKAGE_CLAUDE_AGENT_SDK",
    )


def test_refresh_preview_uses_targeted_packages_and_clean_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    monkeypatch.setenv("UV_EXCLUDE_NEWER", "2026-01-01")

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: dict[str, str],
    ) -> CommandResult:
        seen["command"] = command
        seen["cwd"] = cwd
        seen["env"] = env
        return CommandResult(command=command, returncode=0, stdout="ok")

    result = run_refresh_preview(
        tmp_path,
        ("claude-agent-sdk", "google-antigravity"),
        command_runner=runner,
    )

    assert seen["command"] == build_refresh_preview_command(
        ("claude-agent-sdk", "google-antigravity")
    )
    assert "UV_EXCLUDE_NEWER" not in seen["env"]
    assert result.removed_env == ("UV_EXCLUDE_NEWER",)


def test_collect_evidence_records_versions_and_sources(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """
[[package]]
name = "claude-agent-sdk"
version = "0.2.1"
""",
        encoding="utf-8",
    )

    evidence = collect_evidence(
        tmp_path,
        packages=("claude-agent-sdk",),
        pypi_client=_fake_pypi,
    )

    package = evidence["packages"][0]
    assert package["name"] == "claude-agent-sdk"
    assert package["pyproject_spec"] == "claude-agent-sdk>=0.2"
    assert package["locked_version"] == "0.2.1"
    assert package["latest_version"] == "0.3.0"
    assert package["recent_versions"][:2] == ["0.3.0", "0.2.1"]
    assert package["sources"]
    assert evidence["adapter_sources"]


def test_snapshot_and_diff_public_api(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType("fake_sdk")

    def run(value: str) -> str:
        return value

    module.run = run
    module.__version__ = "1.0.0"
    monkeypatch.setitem(sys.modules, "fake_sdk", module)
    before = snapshot_current_api("fake-sdk")

    def run_new(value: str, *, verbose: bool = False) -> str:
        return value if not verbose else value.upper()

    module.run = run_new
    module.extra = object()
    module.__version__ = "2.0.0"
    after = snapshot_current_api("fake-sdk")

    diff = diff_snapshots(before, after)
    assert diff.added == ("extra",)
    assert diff.changed == ("run",)


def test_schema_validation_rejects_missing_required_field() -> None:
    with pytest.raises(SchemaValidationError):
        validate_mapping({"packages": [], "themes": []}, DIRECTION_ANALYSIS_SCHEMA, name="stage")


@pytest.mark.asyncio
async def test_stage_execution_uses_agent_task_runtime_primitives(tmp_path: Path) -> None:
    runtime = RecordingRuntime()
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=tmp_path / "reports",
        runtime="fake",
        event_log_path=tmp_path / "events.jsonl",
        implementation_enabled=False,
        draft_pr=False,
    )

    output = await run_stage(
        runtime,
        stage="direction-analysis",
        payload={"evidence": {}, "api_diffs": []},
        schema=DIRECTION_ANALYSIS_SCHEMA,
        context=context,
    )

    assert output["uncertainty"] == []
    assert isinstance(runtime.task, AgentTask)
    assert runtime.task.output_schema is DIRECTION_ANALYSIS_SCHEMA
    assert runtime.task.working_directory == tmp_path
    assert runtime.task.permissions.filesystem is FilesystemAccess.READ_ONLY
    assert runtime.task.metadata["stage"] == "direction-analysis"


@pytest.mark.asyncio
async def test_stage_execution_limits_antigravity_reasoning_to_finish_tool(
    tmp_path: Path,
) -> None:
    runtime = RecordingRuntime(kind=AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK)
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=tmp_path / "reports",
        runtime="antigravity-agent-sdk",
        event_log_path=tmp_path / "events.jsonl",
        implementation_enabled=False,
        draft_pr=False,
    )

    await run_stage(
        runtime,
        stage="direction-analysis",
        payload={"evidence": {}, "api_diffs": []},
        schema=DIRECTION_ANALYSIS_SCHEMA,
        context=context,
    )

    assert runtime.task is not None
    assert runtime.task.permissions.allowed_tools == ("finish",)
    assert runtime.task.permissions.filesystem is FilesystemAccess.READ_ONLY


@pytest.mark.asyncio
async def test_stage_execution_fails_closed_without_structured_output(tmp_path: Path) -> None:
    runtime = RecordingRuntime(capabilities=AgentCapabilities(working_directory=True))
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=tmp_path / "reports",
        runtime="fake",
        event_log_path=tmp_path / "events.jsonl",
        implementation_enabled=False,
        draft_pr=False,
    )

    with pytest.raises(StageExecutionError, match="output_schema"):
        await run_stage(
            runtime,
            stage="direction-analysis",
            payload={"evidence": {}, "api_diffs": []},
            schema=DIRECTION_ANALYSIS_SCHEMA,
            context=context,
        )


def test_recursive_self_adaptation_detection_and_gates() -> None:
    assert detects_recursive_impact([{"changed": ["AgentTask"]}])
    architecture = with_recursive_impact(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "recursive_self_adaptation_impact": False,
            "verification_commands": [],
        },
        [{"removed": ["AgentResult"]}],
    )
    assert architecture["recursive_self_adaptation_impact"] is True
    assert architecture["self_adaptation_plan"]

    blocked = evaluate_implementation_gate(
        {
            "safe_to_implement": True,
            "manual_design_required": False,
            "recursive_self_adaptation_impact": True,
        },
        {"status": "pass"},
        implementation_enabled=True,
    )
    assert blocked.allowed is False
    assert "recursive" in blocked.reason

    allowed = evaluate_implementation_gate(
        architecture,
        {"status": "pass"},
        implementation_enabled=True,
    )
    assert allowed.allowed is True


def test_reviewer_rejection_blocks_implementation() -> None:
    gate = evaluate_implementation_gate(
        {
            "safe_to_implement": True,
            "manual_design_required": False,
            "recursive_self_adaptation_impact": False,
        },
        {"status": "reject"},
        implementation_enabled=True,
    )

    assert gate.allowed is False
    assert "reviewer" in gate.reason


@pytest.mark.asyncio
async def test_run_agent_report_only_generates_artifacts(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")

    report_path = await run_agent(
        RunOptions(
            workspace=tmp_path,
            runtime="fake",
            packages=("claude-agent-sdk",),
            report_dir=Path("reports"),
            implementation_enabled=False,
        ),
        pypi_client=_fake_pypi,
        runtime=FixtureEvolutionRuntime(),
    )

    assert report_path.exists()
    assert (report_path.parent / "evidence.json").exists()
    assert (report_path.parent / "api_diffs.json").exists()
    assert (report_path.parent / "direction_analysis.json").exists()
    assert (report_path.parent / "architecture_decision.json").exists()
    assert (report_path.parent / "implementation_summary.json").exists()
    assert (report_path.parent / "review.json").exists()
    assert (report_path.parent / "events.jsonl").exists()
    assert "Recursive self-adaptation impact" in report_path.read_text(encoding="utf-8")


def test_parse_args_and_pr_body() -> None:
    options = parse_args(
        [
            "--runtime",
            "claude-agent-sdk",
            "--package",
            "claude-agent-sdk",
            "--implementation-enabled",
            "--draft-pr",
        ]
    )
    body = build_draft_pr_body("# report")

    assert options.runtime == "claude-agent-sdk"
    assert options.packages == ("claude-agent-sdk",)
    assert options.implementation_enabled is True
    assert options.draft_pr is True
    assert "No auto-merge" in body


def test_build_registry_injects_isolated_codex_home() -> None:
    runtime = build_registry().resolve(AgentRuntimeKind.CODEX_AGENT_SDK)

    assert isinstance(runtime, CodexAgentRuntime)
    assert runtime._env is not None
    assert runtime._env["CODEX_HOME"] == str(SDK_EVOLUTION_CODEX_HOME)


class RecordingRuntime:
    def __init__(
        self,
        capabilities: AgentCapabilities | None = None,
        kind: AgentRuntimeKind = AgentRuntimeKind.FAKE,
    ) -> None:
        self.kind = kind
        self.capabilities = capabilities or AgentCapabilities(
            working_directory=True,
            structured_output=True,
        )
        self.task: AgentTask | None = None

    def availability(self) -> RuntimeAvailability:
        return RuntimeAvailability.ok(self.kind)

    async def run(self, task: AgentTask) -> AgentResult:
        self.task = task
        return AgentResult(
            output="{}",
            parsed_output={"packages": [], "themes": [], "uncertainty": []},
        )

    async def cancel(self, task_id: str) -> None:
        del task_id


def _fake_pypi(package: str) -> dict[str, Any]:
    assert package == "claude-agent-sdk"
    return {
        "info": {"version": "0.3.0"},
        "releases": {
            "0.1.0": [{}],
            "0.2.1": [{}],
            "0.3.0": [{}],
            "0.4.0": [],
        },
    }
