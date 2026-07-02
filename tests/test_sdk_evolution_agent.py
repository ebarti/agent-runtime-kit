from __future__ import annotations

import os
import stat
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
    RuntimeRegistry,
)
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)
from examples.sdk_evolution_agent import auth as auth_module
from examples.sdk_evolution_agent.auth import CodexAuthResult, ensure_codex_sdk_auth
from examples.sdk_evolution_agent.behavior import (
    collect_behavior_evidence,
    diff_behavior_results,
)
from examples.sdk_evolution_agent.cli import RunOptions, _collect_snapshots, parse_args, run_agent
from examples.sdk_evolution_agent.collectors import (
    build_refresh_preview_command,
    collect_evidence,
    cutoff_free_env,
    run_lock_update,
    run_refresh_preview,
)
from examples.sdk_evolution_agent.current_state import build_current_state
from examples.sdk_evolution_agent.models import ApiSnapshot, CommandResult, RunContext
from examples.sdk_evolution_agent.pr import build_draft_pr_body
from examples.sdk_evolution_agent.release_notes import (
    _format_github_discussions_index,
    collect_release_notes,
)
from examples.sdk_evolution_agent.schemas import (
    DIRECTION_ANALYSIS_SCHEMA,
    SchemaValidationError,
    validate_mapping,
)
from examples.sdk_evolution_agent.snapshots import diff_snapshots, snapshot_current_api
from examples.sdk_evolution_agent.stages import (
    SDK_EVOLUTION_CODEX_HOME,
    SDK_EVOLUTION_CODEX_MODEL,
    SDK_EVOLUTION_CODEX_REASONING_EFFORT,
    FixtureEvolutionRuntime,
    StageExecutionError,
    build_registry,
    detects_recursive_impact,
    evaluate_implementation_gate,
    run_stage,
    with_behavior_probe_guard,
    with_candidate_api_diff_guard,
    with_manual_design_gate,
    with_recursive_impact,
    with_release_note_guard,
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


def test_lock_update_uses_targeted_packages_and_clean_env(
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

    result = run_lock_update(
        tmp_path,
        ("claude-agent-sdk", "google-antigravity"),
        command_runner=runner,
    )

    assert seen["command"] == (
        "uv",
        "lock",
        "-P",
        "claude-agent-sdk",
        "-P",
        "google-antigravity",
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


def test_release_notes_collects_matching_update_source() -> None:
    notes = collect_release_notes(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.96",
                "installed_version": "0.2.96",
            }
        ],
        {"claude-agent-sdk": "0.2.106"},
        fetcher=lambda url: "## 0.2.106\n- Added TaskUpdatedMessage\n",
    )

    assert notes[0].status == "found"
    assert notes[0].to_version == "0.2.106"
    assert any("TaskUpdatedMessage" in summary for summary in notes[0].summaries)


def test_antigravity_release_notes_fetches_matching_discussion_from_announcements() -> None:
    def fetcher(url: str) -> str:
        if url.endswith("/discussions/categories/announcements"):
            return (
                '<a href="https://github.com/google-antigravity/antigravity-sdk-python/'
                'discussions/87">'
                "Google Antigravity Python SDK - v0.1.5 Release Notes</a>"
            )
        if url.endswith("/discussions/87"):
            return (
                "## Google Antigravity Python SDK - v0.1.5 Release Notes\n"
                "- OpenTelemetry Tracing Support\n"
                "- Declarative Subagent Configurations with SubagentConfig\n"
                "- Python 3.14 Compatibility\n"
            )
        return "no matching release note"

    notes = collect_release_notes(
        [
            {
                "name": "google-antigravity",
                "locked_version": "0.1.4",
                "installed_version": "0.1.4",
            }
        ],
        {"google-antigravity": "0.1.5"},
        fetcher=fetcher,
    )

    assert notes[0].status == "found"
    assert notes[0].to_version == "0.1.5"
    assert any(url.endswith("/discussions/87") for url in notes[0].checked_urls)
    assert any(source.url and source.url.endswith("/discussions/87") for source in notes[0].sources)
    assert any("OpenTelemetry" in summary for summary in notes[0].summaries)
    assert any("Python 3.14" in summary for summary in notes[0].summaries)


def test_github_discussions_graphql_index_filters_discussion_category() -> None:
    index = _format_github_discussions_index(
        {
            "data": {
                "repository": {
                    "discussions": {
                        "nodes": [
                            {
                                "title": "Google Antigravity Python SDK - v0.1.5 Release Notes",
                                "url": (
                                    "https://github.com/google-antigravity/"
                                    "antigravity-sdk-python/discussions/87"
                                ),
                                "body": "OpenTelemetry tracing and Python 3.14 compatibility.",
                                "category": {"slug": "announcements"},
                            },
                            {
                                "title": "Community Discussion",
                                "url": (
                                    "https://github.com/google-antigravity/"
                                    "antigravity-sdk-python/discussions/43"
                                ),
                                "body": "Not a release note.",
                                "category": {"slug": "show-and-tell"},
                            },
                        ]
                    }
                }
            }
        },
        category_slug="announcements",
    )

    assert "discussions/87" in index
    assert "OpenTelemetry" in index
    assert "discussions/43" not in index


def test_release_notes_record_source_coverage_without_version_match() -> None:
    notes = collect_release_notes(
        [
            {
                "name": "google-antigravity",
                "locked_version": "0.1.2",
                "installed_version": "0.1.2",
            }
        ],
        {"google-antigravity": "0.1.4"},
        fetcher=lambda url: "Google Antigravity product changelog",
    )

    assert notes[0].status == "no-matching-version"
    assert "no package-version-specific" in notes[0].summaries[0]


def test_release_note_guard_blocks_unavailable_update_source() -> None:
    guarded = with_release_note_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        [
            {
                "package": "claude-agent-sdk",
                "to_version": "0.2.106",
                "status": "unavailable",
            }
        ],
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


def test_behavior_diffs_track_candidate_contract_changes() -> None:
    behavior = collect_behavior_evidence(
        [
            {
                "name": "fake-sdk",
                "locked_version": "1.0.0",
                "installed_version": "1.0.0",
            }
        ],
        {},
    )
    assert behavior["summary"]["status"] == "pass"

    diffs = diff_behavior_results(
        [
            _probe("claude-agent-sdk", "0.2.96", "current-environment", "pass", {"fields": ["a"]}),
            _probe("claude-agent-sdk", "0.2.106", "isolated-venv", "fail", {"fields": []}),
        ]
    )

    assert diffs[0].severity == "breaking"


def test_behavior_diffs_ignore_optional_field_churn_when_contract_holds() -> None:
    required = ["api_key", "mcp_servers", "model"]
    diffs = diff_behavior_results(
        [
            _probe(
                "google-antigravity",
                "0.1.2",
                "current-baseline",
                "pass",
                {
                    "fields": ["api_key", "gemini_config", "mcp_servers", "model"],
                    "required_fields": required,
                    "missing": [],
                },
            ),
            _probe(
                "google-antigravity",
                "0.1.4",
                "candidate",
                "pass",
                {
                    "fields": ["api_key", "mcp_servers", "model", "models"],
                    "required_fields": required,
                    "missing": [],
                },
            ),
        ]
    )

    assert diffs[0].severity == "none"
    assert diffs[0].summary == "No behavior contract difference detected."


def test_behavior_evidence_uses_locked_baseline_when_environment_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def isolated(package: str, version: str, *, scope: str = "candidate"):
        calls.append((package, version, scope))
        return (_probe(package, version, scope, "pass", {"scope": scope}),)

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        isolated,
    )

    behavior = collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.96",
                "installed_version": "0.2.106",
            }
        ],
        {"claude-agent-sdk": "0.2.106"},
    )

    assert calls == [
        ("claude-agent-sdk", "0.2.96", "current-baseline"),
        ("claude-agent-sdk", "0.2.106", "candidate"),
    ]
    assert behavior["diffs"][0].severity == "changed"


def test_behavior_probe_guard_blocks_breaking_candidate_diff() -> None:
    guarded = with_behavior_probe_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        {
            "diffs": [
                {
                    "package": "google-antigravity",
                    "probe": "adapter-contract",
                    "severity": "breaking",
                    "summary": "Candidate probe changed from pass to fail.",
                }
            ]
        },
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


def test_current_state_artifact_paths_are_repo_relative(tmp_path: Path) -> None:
    (tmp_path / "uv.lock").write_text(
        """
[[package]]
name = "claude-agent-sdk"
version = "0.2.106"
""",
        encoding="utf-8",
    )
    report_root = tmp_path / "reports" / "sdk-evolution" / "run-1"
    report_root.mkdir(parents=True)
    (report_root / "evidence.json").write_text("{}", encoding="utf-8")
    snapshots = report_root / "api_snapshots"
    snapshots.mkdir()
    (snapshots / "01-claude-agent-sdk.json").write_text("{}", encoding="utf-8")
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=report_root,
        runtime="fake",
        event_log_path=report_root / "events.jsonl",
        implementation_enabled=True,
        draft_pr=False,
    )

    state = build_current_state(
        context,
        promoted=True,
        status="promoted",
        implementation={"applied": True},
    )

    paths = [artifact["path"] for artifact in state["artifacts"].values()]
    assert "reports/sdk-evolution/run-1/evidence.json" in paths
    assert "reports/sdk-evolution/run-1/api_snapshots/01-claude-agent-sdk.json" in paths
    assert all(not path.startswith("/") for path in paths)
    assert all("/private/tmp" not in path and "/tmp/" not in path for path in paths)


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


def test_parse_args_candidate_inspection_is_opt_in() -> None:
    # Candidate inspection pip-installs+imports upstream code, so it is off by
    # default and only enabled with the explicit flag.
    assert parse_args(["--runtime", "fake"]).inspect_candidates is False
    assert parse_args(["--runtime", "fake", "--inspect-candidates"]).inspect_candidates is True


def test_collect_snapshots_uses_lockfile_baseline_for_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def current_snapshot(package: str, *, version: str | None = None) -> ApiSnapshot:
        calls.append(("current", version))
        return ApiSnapshot(package=package, version=version, module="google.antigravity")

    def candidate_snapshot(package: str, version: str) -> ApiSnapshot:
        calls.append(("candidate", version))
        return ApiSnapshot(
            package=package,
            version=version,
            module="google.antigravity",
            source="isolated-venv",
        )

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        current_snapshot,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        candidate_snapshot,
    )

    snapshots = _collect_snapshots(
        {
            "packages": [
                {
                    "name": "google-antigravity",
                    "locked_version": "0.1.2",
                    "installed_version": "0.1.4",
                    "latest_version": "0.1.4",
                }
            ]
        },
        inspect_candidates=True,
    )

    assert len(snapshots) == 2
    assert calls == [("candidate", "0.1.2"), ("candidate", "0.1.4")]


def test_collect_snapshots_uses_refresh_preview_update_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def current_snapshot(package: str, *, version: str | None = None) -> ApiSnapshot:
        calls.append(("current", package, version))
        return ApiSnapshot(package=package, version=version, module=package.replace("-", "_"))

    def candidate_snapshot(package: str, version: str) -> ApiSnapshot:
        calls.append(("candidate", package, version))
        return ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
            source="isolated-venv",
        )

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        current_snapshot,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        candidate_snapshot,
    )

    snapshots = _collect_snapshots(
        {
            "packages": [
                {
                    "name": "claude-agent-sdk",
                    "locked_version": "0.2.96",
                    "installed_version": "0.2.96",
                    "latest_version": "0.2.106",
                },
                {
                    "name": "openai-codex-cli-bin",
                    "locked_version": "0.137.0a4",
                    "installed_version": "0.137.0a4",
                    "latest_version": "0.136.0",
                },
            ],
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update claude-agent-sdk v0.2.96 -> v0.2.106\n",
            },
        },
        inspect_candidates=True,
    )

    assert len(snapshots) == 3
    assert calls == [
        ("current", "claude-agent-sdk", "0.2.96"),
        ("candidate", "claude-agent-sdk", "0.2.106"),
        ("current", "openai-codex-cli-bin", "0.137.0a4"),
    ]


def test_collect_snapshots_uses_locked_baseline_when_environment_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def current_snapshot(package: str, *, version: str | None = None) -> ApiSnapshot:
        calls.append(("current", package, version))
        return ApiSnapshot(package=package, version=version, module=package.replace("-", "_"))

    def isolated_snapshot(package: str, version: str) -> ApiSnapshot:
        calls.append(("isolated", package, version))
        return ApiSnapshot(package=package, version=version, module=package.replace("-", "_"))

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        current_snapshot,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        isolated_snapshot,
    )

    _collect_snapshots(
        {
            "packages": [
                {
                    "name": "claude-agent-sdk",
                    "locked_version": "0.2.96",
                    "installed_version": "0.2.106",
                    "latest_version": "0.2.106",
                },
            ],
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update claude-agent-sdk v0.2.96 -> v0.2.106\n",
            },
        },
        inspect_candidates=True,
    )

    assert calls == [
        ("isolated", "claude-agent-sdk", "0.2.96"),
        ("isolated", "claude-agent-sdk", "0.2.106"),
    ]


def test_candidate_api_diff_guard_blocks_missing_update_diff() -> None:
    guarded = with_candidate_api_diff_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
            "self_adaptation_plan": [],
        },
        {
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update google-antigravity v0.1.2 -> v0.1.4\n",
            }
        },
        [],
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True
    assert "missing api_diffs for google-antigravity" in guarded["findings"][-1]["evidence"]


def test_candidate_api_diff_guard_accepts_empty_update_diff() -> None:
    guarded = with_candidate_api_diff_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
        },
        {
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update google-antigravity v0.1.2 -> v0.1.4\n",
            }
        },
        [
            {
                "package": "google-antigravity",
                "from_version": "0.1.2",
                "to_version": "0.1.4",
                "added": [],
                "removed": [],
                "changed": [],
            }
        ],
    )

    assert guarded["safe_to_implement"] is True
    assert guarded["manual_design_required"] is False


def test_manual_design_gate_forces_safe_to_implement_false() -> None:
    architecture = with_manual_design_gate(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": True,
        }
    )

    assert architecture["safe_to_implement"] is False


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
    # AgentTask copies mapping fields into a read-only dict subclass, so compare
    # by value.
    assert runtime.task.output_schema == DIRECTION_ANALYSIS_SCHEMA
    assert runtime.task.working_directory == tmp_path
    assert runtime.task.permissions.filesystem is FilesystemAccess.READ_ONLY
    assert runtime.task.metadata["stage"] == "direction-analysis"
    assert "model" not in runtime.task.metadata
    assert "reasoning_effort" not in runtime.task.metadata


@pytest.mark.asyncio
async def test_codex_stage_execution_uses_gpt_55_xhigh_thinking(tmp_path: Path) -> None:
    runtime = RecordingRuntime(kind=AgentRuntimeKind.CODEX_AGENT_SDK)
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=tmp_path / "reports",
        runtime="codex-agent-sdk",
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
    assert runtime.task.metadata["model"] == SDK_EVOLUTION_CODEX_MODEL
    assert runtime.task.metadata["reasoning_effort"] == SDK_EVOLUTION_CODEX_REASONING_EFFORT


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


def test_reviewer_approved_status_allows_implementation() -> None:
    gate = evaluate_implementation_gate(
        {
            "safe_to_implement": True,
            "manual_design_required": False,
            "recursive_self_adaptation_impact": False,
        },
        {"status": "approved"},
        implementation_enabled=True,
    )

    assert gate.allowed is True


@pytest.mark.asyncio
async def test_run_agent_report_only_generates_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        lambda package, *, version=None: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
        ),
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        lambda package, version: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
            source="isolated-venv",
        ),
    )

    report_path = await run_agent(
        RunOptions(
            workspace=tmp_path,
            runtime="fake",
            packages=("claude-agent-sdk",),
            report_dir=Path("reports"),
            implementation_enabled=False,
            inspect_candidates=True,
        ),
        pypi_client=_fake_pypi,
        runtime=FixtureEvolutionRuntime(),
    )

    assert report_path.exists()
    assert (report_path.parent / "evidence.json").exists()
    assert (report_path.parent / "release_notes.json").exists()
    assert (report_path.parent / "api_diffs.json").exists()
    assert (report_path.parent / "behavior_probes.json").exists()
    assert (report_path.parent / "behavior_diffs.json").exists()
    assert (report_path.parent / "current_state.json").exists()
    assert (report_path.parent / "direction_analysis.json").exists()
    assert (report_path.parent / "architecture_decision.json").exists()
    assert (report_path.parent / "implementation_summary.json").exists()
    assert (report_path.parent / "review.json").exists()
    assert (report_path.parent / "events.jsonl").exists()
    assert '"package": "claude-agent-sdk"' in (report_path.parent / "api_diffs.json").read_text(
        encoding="utf-8"
    )
    assert "Recursive self-adaptation impact" in report_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_run_agent_does_not_install_candidates_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project.optional-dependencies]\nclaude = ["claude-agent-sdk>=0.2"]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        lambda package, *, version=None: ApiSnapshot(
            package=package, version=version, module=package.replace("-", "_")
        ),
    )

    def forbidden_candidate(package: str, version: str) -> ApiSnapshot:
        raise AssertionError("candidate install must not run without --inspect-candidates")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv", forbidden_candidate
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.collect_release_notes", lambda packages, updates: []
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.collect_behavior_evidence",
        lambda packages, updates: {"results": [], "diffs": [], "summary": {"status": "pass"}},
    )

    # Default run (no inspect_candidates) must never pip-install/import upstream code.
    report_path = await run_agent(
        RunOptions(workspace=tmp_path, runtime="fake", packages=("claude-agent-sdk",)),
        pypi_client=_fake_pypi,
        runtime=FixtureEvolutionRuntime(),
    )

    assert report_path.exists()


def test_finalize_report_skips_when_report_dir_is_gitignored(tmp_path: Path) -> None:
    from examples.sdk_evolution_agent.cli import _commit_final_autonomous_pr_report

    commands: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        del cwd, env
        commands.append(command)
        # check-ignore exits 0 => path IS ignored (the default report dir).
        return CommandResult(command=command, returncode=0)

    report_path = tmp_path / "reports" / "sdk-evolution" / "run" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("x", encoding="utf-8")

    # Must skip cleanly (no add/commit of a gitignored path) instead of raising.
    _commit_final_autonomous_pr_report(
        tmp_path,
        report_path=report_path,
        options=RunOptions(workspace=tmp_path, runtime="fake"),
        command_runner=runner,
    )

    assert not any(command[:2] == ("git", "commit") for command in commands)
    assert not any(command[:2] == ("git", "add") for command in commands)


@pytest.mark.asyncio
async def test_run_agent_autonomous_pr_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        lambda package, *, version=None: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
        ),
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        lambda package, version: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
            source="isolated-venv",
        ),
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.collect_release_notes",
        lambda packages, updates: [],
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.collect_behavior_evidence",
        lambda packages, updates: {"results": [], "diffs": [], "summary": {"status": "pass"}},
    )
    commands: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        del cwd, env
        commands.append(command)
        if command[:3] == ("uv", "lock", "--dry-run"):
            return CommandResult(
                command=command,
                returncode=0,
                stderr="Update claude-agent-sdk v0.2.1 -> v0.3.0\n",
            )
        if command[:2] == ("uv", "lock"):
            return CommandResult(command=command, returncode=0, stdout="updated")
        if command[:2] == ("git", "check-ignore"):
            # Report dir is tracked in this scenario -> not ignored (exit 1).
            return CommandResult(command=command, returncode=1, stdout="")
        return CommandResult(command=command, returncode=0, stdout="ok")

    report_path = await run_agent(
        RunOptions(
            workspace=tmp_path,
            runtime="fake",
            packages=("claude-agent-sdk",),
            report_dir=Path("reports"),
            inspect_candidates=True,
            implementation_enabled=True,
            refresh_preview=True,
            create_branch=True,
            branch_name="sdk-update-test",
            draft_pr=True,
            pr_base="main",
        ),
        pypi_client=_fake_pypi,
        command_runner=runner,
        runtime=PermissiveRuntime(),
    )

    assert report_path.exists()
    assert ("git", "switch", "-c", "sdk-update-test") in commands
    assert ("uv", "lock", "-P", "claude-agent-sdk") in commands
    assert any(command[:3] == ("git", "commit", "-m") for command in commands)
    assert any(command[:4] == ("gh", "pr", "create", "--draft") for command in commands)
    assert ("git", "commit", "-m", "Finalize SDK evolution report") in commands
    assert commands.count(("git", "push", "-u", "origin", "sdk-update-test")) == 2
    pr_index = next(
        i for i, command in enumerate(commands) if command[:3] == ("gh", "pr", "create")
    )
    finalize_index = commands.index(("git", "commit", "-m", "Finalize SDK evolution report"))
    assert finalize_index > pr_index


@pytest.mark.asyncio
async def test_run_agent_closes_owned_runtime_on_stage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        """
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2"]
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        lambda package, *, version=None: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
        ),
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        lambda package, version: ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
            source="isolated-venv",
        ),
    )

    class ClosingRuntime(RecordingRuntime):
        closed = False

        async def run(self, task: AgentTask) -> AgentResult:
            self.task = task
            raise RuntimeError("stage boom")

        async def aclose(self) -> None:
            self.closed = True

    runtime = ClosingRuntime()
    registry = RuntimeRegistry()
    registry.register(AgentRuntimeKind.FAKE, lambda: runtime)

    with pytest.raises(StageExecutionError, match="direction-analysis failed"):
        await run_agent(
            RunOptions(
                workspace=tmp_path,
                runtime="fake",
                packages=("claude-agent-sdk",),
                report_dir=Path("reports"),
                implementation_enabled=False,
                inspect_candidates=False,
            ),
            pypi_client=_fake_pypi,
            registry=registry,
        )

    assert runtime.closed is True


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
    assert options.inspect_candidates is False
    assert options.implementation_enabled is True
    assert options.draft_pr is True
    assert "No auto-merge" in body


def test_build_registry_configures_vendor_process_reuse() -> None:
    registry = build_registry()
    runtime = registry.resolve(AgentRuntimeKind.CODEX_AGENT_SDK)

    assert isinstance(runtime, CodexAgentRuntime)
    assert runtime._default_model == SDK_EVOLUTION_CODEX_MODEL
    assert runtime._reuse_process is True
    assert runtime._env is not None
    assert runtime._env["CODEX_HOME"] == str(SDK_EVOLUTION_CODEX_HOME)

    claude = registry.resolve(AgentRuntimeKind.CLAUDE_AGENT_SDK)
    assert isinstance(claude, ClaudeAgentRuntime)
    assert claude._reuse_process is True

    antigravity = registry.resolve(AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK)
    assert isinstance(antigravity, AntigravityAgentRuntime)
    assert antigravity._reuse_process is True


def test_codex_auth_helper_copies_newer_primary_auth_to_dedicated_home(
    tmp_path: Path,
) -> None:
    source_auth = tmp_path / ".codex" / "auth.json"
    target_auth = tmp_path / "codex-home" / "auth.json"
    source_auth.parent.mkdir()
    target_auth.parent.mkdir()
    source_auth.write_text('{"access_token":"fresh"}', encoding="utf-8")
    target_auth.write_text('{"access_token":"stale"}', encoding="utf-8")
    os.utime(source_auth, (200.0, 200.0))
    os.utime(target_auth, (100.0, 100.0))
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def runner(
        command: tuple[str, ...],
        *,
        env: dict[str, str],
        timeout: int = 60,
    ) -> CommandResult:
        del timeout
        calls.append((command, env))
        return CommandResult(command=command, returncode=0, stdout="Logged in using ChatGPT\n")

    result = ensure_codex_sdk_auth(
        codex_home=tmp_path / "codex-home",
        home_dir=tmp_path,
        env={
            "CODEX_HOME": str(tmp_path / "ambient-codex"),
            "UV_EXCLUDE_NEWER": "2026-01-01",
            "UV_EXCLUDE_NEWER_PACKAGE_OPENAI_CODEX": "2026-01-01",
        },
        command_runner=runner,
    )

    assert result.ok is True
    assert result.auth_copied is True
    assert result.removed_env == (
        "UV_EXCLUDE_NEWER",
        "UV_EXCLUDE_NEWER_PACKAGE_OPENAI_CODEX",
    )
    assert calls == [
        (
            ("codex", "login", "status"),
            {"CODEX_HOME": str(tmp_path / "codex-home")},
        )
    ]
    assert target_auth.read_text(encoding="utf-8") == '{"access_token":"fresh"}'
    assert stat.S_IMODE(target_auth.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target_auth.stat().st_mode) == 0o600


def test_codex_auth_helper_keeps_newer_dedicated_auth(tmp_path: Path) -> None:
    source_auth = tmp_path / ".codex" / "auth.json"
    target_auth = tmp_path / "codex-home" / "auth.json"
    source_auth.parent.mkdir()
    target_auth.parent.mkdir()
    source_auth.write_text('{"access_token":"primary"}', encoding="utf-8")
    target_auth.write_text('{"access_token":"isolated"}', encoding="utf-8")
    target_auth.parent.chmod(0o755)
    target_auth.chmod(0o644)
    os.utime(source_auth, (100.0, 100.0))
    os.utime(target_auth, (200.0, 200.0))

    def runner(
        command: tuple[str, ...],
        *,
        env: dict[str, str],
        timeout: int = 60,
    ) -> CommandResult:
        del env, timeout
        return CommandResult(command=command, returncode=0, stdout="Logged in using ChatGPT\n")

    result = ensure_codex_sdk_auth(
        codex_home=tmp_path / "codex-home",
        home_dir=tmp_path,
        env={},
        command_runner=runner,
    )

    assert result.ok is True
    assert result.auth_copied is False
    assert target_auth.read_text(encoding="utf-8") == '{"access_token":"isolated"}'
    assert stat.S_IMODE(target_auth.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target_auth.stat().st_mode) == 0o600


def test_codex_auth_helper_does_not_use_token_or_api_key_env(tmp_path: Path) -> None:
    source_auth = tmp_path / ".codex" / "auth.json"
    source_auth.parent.mkdir()
    source_auth.write_text('{"access_token":"fresh"}', encoding="utf-8")
    calls: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        *,
        env: dict[str, str],
        timeout: int = 60,
    ) -> CommandResult:
        del env, timeout
        calls.append(command)
        return CommandResult(command=command, returncode=0, stdout="Logged in using ChatGPT\n")

    result = ensure_codex_sdk_auth(
        codex_home=tmp_path / "codex-home",
        home_dir=tmp_path,
        env={"CODEX_ACCESS_TOKEN": "token-value", "OPENAI_API_KEY": "api-key"},
        command_runner=runner,
    )

    assert result.ok is True
    assert calls == [("codex", "login", "status")]


def test_codex_auth_helper_blocks_when_copied_cache_is_not_authenticated(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        *,
        env: dict[str, str],
        timeout: int = 60,
    ) -> CommandResult:
        del env, timeout
        calls.append(command)
        return CommandResult(command=command, returncode=1, stdout="Not logged in\n")

    result = ensure_codex_sdk_auth(
        codex_home=tmp_path / "codex-home",
        home_dir=tmp_path,
        env={},
        command_runner=runner,
    )

    assert result.ok is False
    assert calls == [("codex", "login", "status")]
    assert "codex login --device-auth" in result.message
    assert "CODEX_ACCESS_TOKEN" not in result.message
    assert "OPENAI_API_KEY" not in result.message


def test_codex_auth_cli_uses_codex_home_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Path] = {}

    def fake_ensure(*, codex_home: Path) -> CodexAuthResult:
        seen["codex_home"] = codex_home
        command = ("codex", "login", "status")
        status = CommandResult(command=command, returncode=0)
        return CodexAuthResult(
            ok=True,
            codex_home=codex_home,
            initial_status=status,
            final_status=status,
            message="ready",
        )

    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "from-env"))
    monkeypatch.setattr(auth_module, "ensure_codex_sdk_auth", fake_ensure)

    assert auth_module.main(["ensure-codex"]) == 0
    assert seen["codex_home"] == tmp_path / "from-env"


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


class PermissiveRuntime(RecordingRuntime):
    async def run(self, task: AgentTask) -> AgentResult:
        self.task = task
        stage = task.metadata["stage"]
        if stage == "direction-analysis":
            payload = {"packages": [], "themes": [], "uncertainty": []}
        elif stage == "architecture-decision":
            payload = {
                "findings": [],
                "safe_to_implement": True,
                "manual_design_required": False,
                "recursive_self_adaptation_impact": False,
                "self_adaptation_plan": ["Update SDK lockfile."],
                "verification_commands": [],
                "uncertainty": [],
            }
        elif stage == "review":
            payload = {"status": "pass", "reasons": [], "required_changes": []}
        else:
            payload = {
                "applied": False,
                "changes": [],
                "verification_results": [],
                "blocked_reason": "",
            }
        return AgentResult(output="{}", parsed_output=payload)


def _probe(
    package: str,
    version: str,
    scope: str,
    status: str,
    details: dict[str, Any],
):
    from examples.sdk_evolution_agent.models import BehaviorProbeResult

    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe="adapter-contract",
        status=status,
        summary=status,
        details=details,
    )


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
