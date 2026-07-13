from __future__ import annotations

import json
import os
import stat
import subprocess
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
from examples.sdk_evolution_agent import behavior as behavior_module
from examples.sdk_evolution_agent.auth import CodexAuthResult, ensure_codex_sdk_auth
from examples.sdk_evolution_agent.behavior import (
    assess_behavior_payload,
    collect_behavior_evidence,
    diff_behavior_results,
    probe_candidate_in_venv,
    probe_current_package,
    summarize_behavior,
)
from examples.sdk_evolution_agent.cli import RunOptions, _collect_snapshots, parse_args, run_agent
from examples.sdk_evolution_agent.collectors import (
    ResolverTransition,
    build_refresh_preview_command,
    collect_evidence,
    cutoff_free_env,
    parse_refresh_transitions,
    run_lock_update,
    run_refresh_preview,
)
from examples.sdk_evolution_agent.current_state import build_current_state
from examples.sdk_evolution_agent.models import (
    ApiSnapshot,
    BehaviorDiff,
    BehaviorProbeResult,
    CommandResult,
    RunContext,
    SourceRef,
    to_jsonable,
)
from examples.sdk_evolution_agent.pr import build_draft_pr_body
from examples.sdk_evolution_agent.release_notes import (
    _fetch_source_text,
    _format_github_discussions_index,
    collect_release_notes,
)
from examples.sdk_evolution_agent.report import render_markdown_report, write_run_report
from examples.sdk_evolution_agent.schemas import (
    DIRECTION_ANALYSIS_SCHEMA,
    SchemaValidationError,
    validate_mapping,
)
from examples.sdk_evolution_agent.snapshots import (
    DEFAULT_MODULES,
    diff_snapshots,
    snapshot_candidate_in_venv,
    snapshot_current_api,
)
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


def test_refresh_transition_parser_reads_exact_stdout_and_stderr_updates() -> None:
    transitions = parse_refresh_transitions(
        {
            "refresh_preview": {
                "stdout": (
                    "Update claude-agent-sdk v0.2.96 -> v0.2.106\n"
                    "Update openai-codex v0.1.0b3 -> v0.1.0rc1\n"
                ),
                "stderr": (
                    "Update google-antigravity v0.1.2 -> v0.1.4\n"
                    "Update claude-agent-sdk v0.2.96 -> v0.2.106\n"
                ),
            }
        }
    )

    assert transitions == (
        ResolverTransition("claude-agent-sdk", "0.2.96", "0.2.106"),
        ResolverTransition("google-antigravity", "0.1.2", "0.1.4"),
        ResolverTransition("openai-codex", "0.1.0b3", "0.1.0rc1"),
    )


def test_refresh_transition_parser_rejects_partial_or_decorated_lines() -> None:
    transitions = parse_refresh_transitions(
        {
            "refresh_preview": {
                "stdout": (
                    "NotUpdate claude-agent-sdk v1 -> v2\n"
                    "Update claude-agent-sdk v1 -> v2 trailing\n"
                    "prefix Update claude-agent-sdk v1 -> v2\n"
                    "Update claude-agent-sdk-extra v1 -> v2\n"
                ),
                "stderr": "",
            }
        }
    )

    assert transitions == (ResolverTransition("claude-agent-sdk-extra", "1", "2"),)


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


def test_release_note_links_only_follow_github_https_urls() -> None:
    # The discussion-index markup can carry user-generated hrefs. A
    # protocol-relative path would urljoin into an off-site host and the blind
    # follow-up GET would become SSRF; only https://github.com links may be
    # followed.
    def fetcher(url: str) -> str:
        if url.endswith("/discussions/categories/announcements"):
            return (
                '<a href="//evil.example/x/discussions/87">v0.1.5</a>\n'
                '<a href="https://github.com/google-antigravity/antigravity-sdk-python/'
                'discussions/88">v0.1.5 release notes</a>'
            )
        if "evil.example" in url:
            raise AssertionError("off-site link must not be fetched")
        if url.endswith("/discussions/88"):
            return "## v0.1.5\n- Real release note\n"
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

    assert all("evil.example" not in url for url in notes[0].checked_urls)
    assert any(url.endswith("/discussions/88") for url in notes[0].checked_urls)


def test_fetch_source_text_surfaces_graphql_failure_when_token_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A configured token that fails must surface (the caller records it on the
    # source), not silently downgrade to the unauthenticated HTML scrape — that
    # would hide an expired/insufficient token behind lower-quality evidence.
    monkeypatch.setenv("GITHUB_TOKEN", "configured-but-broken")

    def graphql_fails(url: str, *, token: str) -> str:
        raise RuntimeError("GraphQL: Bad credentials")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.release_notes._fetch_github_discussions_index",
        graphql_fails,
    )
    source = SourceRef(
        kind="github-discussions",
        label="announcements",
        url="https://github.com/o/r/discussions/categories/announcements",
    )

    with pytest.raises(RuntimeError, match="Bad credentials"):
        _fetch_source_text(source, fetcher=lambda url: "html", use_github_graphql=True)


def test_fetch_source_text_uses_plain_fetch_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Tokenless operation is legitimate: without GITHUB_TOKEN/GH_TOKEN the HTML
    # fetch is the normal path, not a downgrade.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    source = SourceRef(
        kind="github-discussions",
        label="announcements",
        url="https://github.com/o/r/discussions/categories/announcements",
    )

    text = _fetch_source_text(source, fetcher=lambda url: "html fallback", use_github_graphql=True)

    assert text == "html fallback"


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
    assert behavior["summary"]["status"] == "incomplete"

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
        inspect_candidates=True,
    )

    assert calls == [
        ("claude-agent-sdk", "0.2.96", "current-baseline"),
        ("claude-agent-sdk", "0.2.106", "candidate"),
    ]
    assert behavior["diffs"][0].severity == "changed"
    assert behavior["expected_packages"] == ["claude-agent-sdk"]
    assert behavior["expected_transitions"] == [
        {
            "package": "claude-agent-sdk",
            "from_version": "0.2.96",
            "to_version": "0.2.106",
        }
    ]


def test_behavior_evidence_uses_locked_baseline_when_sdk_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def isolated(package: str, version: str, *, scope: str = "candidate"):
        calls.append((package, version, scope))
        return (_probe(package, version, scope, "pass", {"scope": scope}),)

    def current(package: str, *, version: str | None = None):
        raise AssertionError(f"ambient probe must not represent {package} {version}")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        isolated,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        current,
    )

    collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.106",
                "installed_version": None,
            }
        ],
        {"claude-agent-sdk": "0.2.110"},
        inspect_candidates=True,
    )

    assert calls == [
        ("claude-agent-sdk", "0.2.106", "current-baseline"),
        ("claude-agent-sdk", "0.2.110", "candidate"),
    ]


def test_behavior_evidence_without_opt_in_reports_actual_ambient_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def current(package: str, *, version: str | None = None):
        calls.append((package, version))
        return (_probe(package, version, "current-environment", "fail", {}),)

    def isolated(package: str, version: str, *, scope: str = "candidate"):
        raise AssertionError(f"isolated probe must not run for {package} {version} {scope}")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        current,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        isolated,
    )

    behavior = collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.106",
                "installed_version": None,
            }
        ],
        {"claude-agent-sdk": "0.2.110"},
    )

    assert calls == [("claude-agent-sdk", None)]
    assert [result.status for result in behavior["results"]] == ["fail", "skip"]


def test_behavior_evidence_without_opt_in_uses_drifted_installed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []

    def current(package: str, *, version: str | None = None):
        calls.append((package, version))
        return (_probe(package, version, "current-environment", "pass", {}),)

    def isolated(package: str, version: str, *, scope: str = "candidate"):
        raise AssertionError(f"isolated probe must not run for {package} {version} {scope}")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        current,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        isolated,
    )

    collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.96",
                "installed_version": "0.2.106",
            }
        ],
        {"claude-agent-sdk": "0.2.110"},
    )

    assert calls == [("claude-agent-sdk", "0.2.106")]


def test_behavior_no_update_is_incomplete_when_ambient_version_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        lambda package, *, version=None: (
            _probe(package, version, "current-environment", "pass", {"missing": []}),
        ),
    )

    behavior = collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "1.0.0",
                "installed_version": "2.0.0",
            }
        ],
        {},
    )

    assert behavior["expected_baselines"] == {"claude-agent-sdk": "1.0.0"}
    assert behavior["summary"]["status"] == "incomplete"
    assert behavior["summary"]["missing_comparison_count"] == 1
    assert "observed: 2.0.0" in " ".join(behavior["summary"]["reasons"])


def test_behavior_no_update_passes_when_ambient_matches_locked_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        lambda package, *, version=None: (
            _probe(package, version, "current-environment", "pass", {"missing": []}),
        ),
    )

    behavior = collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "1.0.0",
                "installed_version": "1.0.0",
            }
        ],
        {},
    )

    assert behavior["summary"]["status"] == "pass"
    assert behavior["summary"]["missing_comparison_count"] == 0


def test_behavior_evidence_reuses_matching_ambient_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def current(package: str, *, version: str | None = None):
        calls.append(("current", package, version))
        return (_probe(package, version, "current-environment", "pass", {}),)

    def isolated(package: str, version: str, *, scope: str = "candidate"):
        calls.append((scope, package, version))
        return (_probe(package, version, scope, "pass", {}),)

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_current_package",
        current,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        isolated,
    )

    collect_behavior_evidence(
        [
            {
                "name": "claude-agent-sdk",
                "locked_version": "0.2.106",
                "installed_version": "0.2.106",
            }
        ],
        {"claude-agent-sdk": "0.2.110"},
        inspect_candidates=True,
    )

    assert calls == [
        ("current", "claude-agent-sdk", "0.2.106"),
        ("candidate", "claude-agent-sdk", "0.2.110"),
    ]


def test_behavior_candidate_probes_are_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    # Probing a candidate pip-installs and imports freshly downloaded upstream
    # code; without --inspect-candidates that must never happen, and the gap
    # must be an explicit skip record rather than a silent evidence hole or a
    # phantom "breaking" diff.
    def explode(package: str, version: str, *, scope: str = "candidate") -> tuple[Any, ...]:
        raise AssertionError("venv probe must not run without --inspect-candidates")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.behavior.probe_candidate_in_venv",
        explode,
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

    candidate_results = [r for r in behavior["results"] if r.scope == "candidate"]
    assert [r.status for r in candidate_results] == ["skip"]
    assert "--inspect-candidates" in candidate_results[0].summary
    # The drifted lockfile baseline falls back to the installed environment.
    assert any(r.scope == "current-environment" for r in behavior["results"])
    assert behavior["summary"]["breaking_count"] == 0


def test_behavior_diffs_ignore_one_sided_skip() -> None:
    diffs = diff_behavior_results(
        [
            _probe("claude-agent-sdk", "0.2.96", "current-environment", "pass", {"fields": ["a"]}),
            _probe("claude-agent-sdk", "0.2.106", "candidate", "skip", {"reason": "opt-in"}),
        ]
    )

    assert diffs == ()


def test_candidate_behavior_probe_scrubs_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[dict[str, str] | None] = []

    def fake_run(args: Any, **kwargs: Any) -> Any:
        captured_envs.append(kwargs.get("env"))
        return types.SimpleNamespace(
            stdout=(
                '[{"package":"claude-agent-sdk","version":"9.9.9",'
                '"scope":"candidate","probe":"adapter-contract","status":"pass",'
                '"summary":"ok","details":{}}]'
            ),
            returncode=0,
        )

    monkeypatch.setenv("ARK_FAKE_SECRET", "not-for-candidates")
    monkeypatch.setattr("examples.sdk_evolution_agent.behavior.subprocess.run", fake_run)

    results = probe_candidate_in_venv("claude-agent-sdk", "9.9.9")

    assert len(results) == 1
    assert results[0].status == "pass"
    # venv create, pip install, probe script: every subprocess that touches the
    # freshly downloaded candidate runs with the scrubbed environment.
    assert len(captured_envs) == 3
    for env in captured_envs:
        assert env is not None
        assert "ARK_FAKE_SECRET" not in env
        assert env.get("HOME") != os.environ.get("HOME")


@pytest.mark.parametrize(
    ("package", "failure_call", "error", "failure_step", "probe"),
    [
        (
            "claude-agent-sdk",
            1,
            subprocess.CalledProcessError(
                1,
                ("python", "-m", "venv"),
                stderr="venv failed " + ("x" * 1_000),
            ),
            "virtual-environment-creation",
            "adapter-contract",
        ),
        (
            "openai-codex-cli-bin",
            2,
            OSError("installer unavailable " + ("x" * 1_000)),
            "package-installation",
            "binary-distribution",
        ),
        (
            "google-antigravity",
            3,
            subprocess.TimeoutExpired(("python", "-c", "probe"), 7),
            "probe-execution",
            "adapter-contract",
        ),
    ],
)
def test_candidate_behavior_probe_contains_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch,
    package: str,
    failure_call: int,
    error: BaseException,
    failure_step: str,
    probe: str,
) -> None:
    calls = 0

    def fake_run(args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise error
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("examples.sdk_evolution_agent.behavior.subprocess.run", fake_run)

    results = probe_candidate_in_venv(package, "9.9.9", timeout=7)

    assert len(results) == 1
    result = results[0]
    assert result.package == package
    assert result.version == "9.9.9"
    assert result.scope == "candidate"
    assert result.probe == probe
    assert result.status == "fail"
    assert result.details["failure_step"] == failure_step
    assert result.details["error"] == result.summary
    assert len(result.summary) <= 560


@pytest.mark.parametrize(
    "output",
    [
        "not-json",
        "[]",
        (
            '[{"package":"wrong-sdk","version":"9.9.9","scope":"candidate",'
            '"probe":"adapter-contract","status":"pass","summary":"ok","details":{}}]'
        ),
        (
            '[{"package":"claude-agent-sdk","version":"9.9.9","scope":"candidate",'
            '"probe":"adapter-contract","status":"pass","summary":null,"details":{}}]'
        ),
        (
            '[{"package":"claude-agent-sdk","version":"9.9.9","scope":"candidate",'
            '"probe":"adapter-contract","status":"unknown","summary":"ok","details":{}}]'
        ),
        (
            '[{"package":"claude-agent-sdk","version":"9.9.9","scope":"candidate",'
            '"probe":"adapter-contract","status":"fail","summary":"missing",'
            '"details":{"missing":7}}]'
        ),
    ],
)
def test_candidate_behavior_probe_contains_malformed_output(
    monkeypatch: pytest.MonkeyPatch,
    output: str,
) -> None:
    def fake_run(args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return types.SimpleNamespace(stdout=output, stderr="", returncode=0)

    monkeypatch.setattr("examples.sdk_evolution_agent.behavior.subprocess.run", fake_run)

    (result,) = probe_candidate_in_venv("claude-agent-sdk", "9.9.9")

    assert result.package == "claude-agent-sdk"
    assert result.version == "9.9.9"
    assert result.scope == "candidate"
    assert result.probe == "adapter-contract"
    assert result.status == "fail"
    assert result.details["failure_step"] == "probe-output-validation"
    assert "malformed probe output" in result.summary
    assert len(result.summary) <= 560


@pytest.mark.parametrize(
    ("private_path", "secret_suffix"),
    [
        ("/tmp/private folder/POSIX_SECRET_SUFFIX", "POSIX_SECRET_SUFFIX"),
        (r"C:\Program Files\Private\DRIVE_SECRET_SUFFIX", "DRIVE_SECRET_SUFFIX"),
        (r"\\server\Private Share\UNC_SECRET_SUFFIX", "UNC_SECRET_SUFFIX"),
        (
            "https://example.invalid/private folder/URL_SECRET_SUFFIX",
            "URL_SECRET_SUFFIX",
        ),
    ],
)
def test_codex_cli_exception_redaction_discards_entire_path_suffix(
    private_path: str,
    secret_suffix: str,
) -> None:
    detail = behavior_module._safe_exception_detail(
        ValueError(
            f"REDACTION_SENTINEL at {private_path} trailing {secret_suffix} "
            + ("x" * 1_000)
        )
    )

    assert detail == "ValueError: REDACTION_SENTINEL at <path>"
    assert private_path not in detail
    assert secret_suffix not in detail
    assert len(detail) <= 240


@pytest.mark.parametrize(
    ("failure", "expected_summary"),
    [
        ("metadata", "RuntimeError: METADATA_SENTINEL"),
        ("module", "ModuleNotFoundError: MODULE_SENTINEL"),
        ("helper-missing", "bundled_codex_path() failed"),
        ("helper-error", "ValueError: HELPER_SENTINEL"),
        ("invalid-return", "TypeError"),
        ("missing-file", "existing regular file"),
        ("directory", "existing regular file"),
    ],
)
def test_codex_cli_binary_probe_contains_each_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_summary: str,
) -> None:
    metadata_path = tmp_path / "private metadata" / "METADATA_SECRET_SUFFIX"
    drive_path = r"C:\Program Files\Private\MODULE_SECRET_SUFFIX"
    unc_path = r"\\server\Private Share\HELPER_SECRET_SUFFIX"

    def metadata_version(package: str) -> str:
        assert package == "openai-codex-cli-bin"
        if failure == "metadata":
            raise RuntimeError(
                f"METADATA_SENTINEL at {metadata_path} " + ("m" * 1_000)
            )
        return "1.2.3"

    def helper_error() -> Path:
        raise ValueError(
            f"HELPER_SENTINEL at {unc_path} " + ("h" * 1_000)
        )

    module = types.SimpleNamespace(bundled_codex_path=lambda: tmp_path / "codex")
    if failure == "helper-missing":
        module = types.SimpleNamespace()
    elif failure == "helper-error":
        module = types.SimpleNamespace(bundled_codex_path=helper_error)
    elif failure == "invalid-return":
        module = types.SimpleNamespace(bundled_codex_path=lambda: None)
    elif failure == "directory":
        module = types.SimpleNamespace(bundled_codex_path=lambda: tmp_path)

    def import_module(name: str) -> Any:
        assert name == "codex_cli_bin"
        if failure == "module":
            raise ModuleNotFoundError(
                f"MODULE_SENTINEL: No module named codex_cli_bin at {drive_path} "
                + ("i" * 1_000)
            )
        return module

    monkeypatch.setattr(behavior_module.importlib.metadata, "version", metadata_version)
    monkeypatch.setattr(behavior_module.importlib, "import_module", import_module)

    (result,) = probe_current_package("openai-codex-cli-bin", version="1.2.3")

    assert result.probe == "binary-distribution"
    assert result.status == "fail"
    assert expected_summary in result.summary
    assert str(tmp_path) not in result.summary
    assert drive_path not in result.summary
    assert unc_path not in result.summary
    serialized_details = json.dumps(result.details)
    assert str(tmp_path) not in serialized_details
    assert drive_path not in serialized_details
    assert unc_path not in serialized_details
    for secret in (
        "METADATA_SECRET_SUFFIX",
        "MODULE_SECRET_SUFFIX",
        "HELPER_SECRET_SUFFIX",
    ):
        assert secret not in result.summary
        assert secret not in serialized_details
    assert result.details["error"] == result.summary
    assert len(result.summary) <= 240
    assert len(result.details["error"]) <= 240
    if failure in {"metadata", "module", "helper-error"}:
        assert "<path>" in result.summary


@pytest.mark.parametrize(
    ("failure", "expected_summary", "secret_suffix"),
    [
        ("conversion", "ValueError: CONVERSION_SENTINEL", "CONVERSION_SECRET_SUFFIX"),
        ("file-check", "OSError: FILE_CHECK_SENTINEL", "FILE_CHECK_SECRET_SUFFIX"),
    ],
)
def test_codex_cli_binary_probe_redacts_path_conversion_and_file_check_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_summary: str,
    secret_suffix: str,
) -> None:
    conversion_url = (
        "https://example.invalid/private folder/CONVERSION_SECRET_SUFFIX"
    )
    file_check_path = tmp_path / "private file check" / "FILE_CHECK_SECRET_SUFFIX"

    class InvalidPath:
        def __fspath__(self) -> str:
            raise ValueError(
                f"CONVERSION_SENTINEL at {conversion_url} " + ("c" * 1_000)
            )

    if failure == "file-check":

        def fail_file_check(self: Path) -> bool:
            del self
            raise OSError(
                f"FILE_CHECK_SENTINEL at {file_check_path} " + ("f" * 1_000)
            )

        monkeypatch.setattr(Path, "is_file", fail_file_check)

    raw_path: object = InvalidPath() if failure == "conversion" else tmp_path / "codex"
    module = types.SimpleNamespace(bundled_codex_path=lambda: raw_path)
    monkeypatch.setattr(
        behavior_module.importlib.metadata,
        "version",
        lambda package: "1.2.3",
    )
    monkeypatch.setattr(
        behavior_module.importlib,
        "import_module",
        lambda name: module,
    )

    (result,) = probe_current_package("openai-codex-cli-bin", version="1.2.3")

    assert result.probe == "binary-distribution"
    assert result.status == "fail"
    assert expected_summary in result.summary
    assert "<path>" in result.summary
    assert secret_suffix not in result.summary
    assert secret_suffix not in json.dumps(result.details)
    assert result.details["error"] == result.summary
    assert len(result.summary) <= 240
    assert len(result.details["error"]) <= 240


def test_codex_cli_binary_probe_requires_only_a_regular_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "codex"
    binary.write_bytes(b"bundled binary")
    module = types.SimpleNamespace(bundled_codex_path=lambda: binary)
    monkeypatch.setattr(
        behavior_module.importlib.metadata,
        "version",
        lambda package: "1.2.3",
    )
    monkeypatch.setattr(
        behavior_module.importlib,
        "import_module",
        lambda name: module,
    )

    (result,) = probe_current_package("openai-codex-cli-bin", version="1.2.3")

    assert result.status == "pass"
    assert result.probe == "binary-distribution"
    assert result.details == {
        "installed_version": "1.2.3",
        "module": "codex_cli_bin",
        "bundled_binary": "regular-file",
    }
    assert str(binary) not in json.dumps(result.details)


@pytest.mark.parametrize(
    ("failure", "expected_summary", "secret_suffix"),
    [
        (
            "metadata",
            "RuntimeError: EMBEDDED_METADATA_SENTINEL",
            "EMBEDDED_METADATA_SECRET_SUFFIX",
        ),
        (
            "module",
            "ModuleNotFoundError: EMBEDDED_MODULE_SENTINEL",
            "EMBEDDED_MODULE_SECRET_SUFFIX",
        ),
        (
            "helper-error",
            "ValueError: EMBEDDED_HELPER_SENTINEL",
            "EMBEDDED_HELPER_SECRET_SUFFIX",
        ),
        (
            "conversion-error",
            "ValueError: EMBEDDED_CONVERSION_SENTINEL",
            "EMBEDDED_CONVERSION_SECRET_SUFFIX",
        ),
        (
            "file-check-error",
            "OSError: EMBEDDED_FILE_CHECK_SENTINEL",
            "EMBEDDED_FILE_CHECK_SECRET_SUFFIX",
        ),
        ("missing-file", "existing regular file", None),
    ],
)
def test_embedded_codex_cli_failures_keep_binary_probe_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str,
    expected_summary: str,
    secret_suffix: str | None,
) -> None:
    metadata_path = (
        tmp_path / "private metadata" / "EMBEDDED_METADATA_SECRET_SUFFIX"
    )
    drive_path = r"D:\Program Files\Private\EMBEDDED_MODULE_SECRET_SUFFIX"
    unc_path = r"\\candidate-host\Private Share\EMBEDDED_HELPER_SECRET_SUFFIX"
    conversion_url = (
        "https://example.invalid/private folder/EMBEDDED_CONVERSION_SECRET_SUFFIX"
    )
    file_check_path = (
        tmp_path / "private file check" / "EMBEDDED_FILE_CHECK_SECRET_SUFFIX"
    )

    def fail_metadata(package: str) -> str:
        if failure == "metadata":
            raise RuntimeError(
                f"EMBEDDED_METADATA_SENTINEL for {package} at {metadata_path} "
                + ("m" * 1_000)
            )
        return "1.2.3"

    def helper_error() -> Path:
        raise ValueError(
            f"EMBEDDED_HELPER_SENTINEL at {unc_path} " + ("h" * 1_000)
        )

    class InvalidPath:
        def __fspath__(self) -> str:
            raise ValueError(
                f"EMBEDDED_CONVERSION_SENTINEL at {conversion_url} "
                + ("c" * 1_000)
            )

    def bundled_path() -> object:
        if failure == "helper-error":
            return helper_error()
        if failure == "conversion-error":
            return InvalidPath()
        return tmp_path / "missing"

    if failure == "file-check-error":

        def fail_file_check(self: Path) -> bool:
            del self
            raise OSError(
                f"EMBEDDED_FILE_CHECK_SENTINEL at {file_check_path} "
                + ("f" * 1_000)
            )

        monkeypatch.setattr(Path, "is_file", fail_file_check)

    def import_module(name: str) -> Any:
        assert name == "codex_cli_bin"
        if failure == "module":
            raise ModuleNotFoundError(
                f"EMBEDDED_MODULE_SENTINEL at {drive_path} " + ("i" * 1_000)
            )
        return module

    helper = bundled_path
    module = types.SimpleNamespace(bundled_codex_path=helper)

    monkeypatch.setattr(behavior_module.importlib.metadata, "version", fail_metadata)
    monkeypatch.setattr(behavior_module.importlib, "import_module", import_module)
    monkeypatch.setattr(
        sys,
        "argv",
        ["probe", "openai-codex-cli-bin", "1.2.3", "candidate"],
    )

    exec(behavior_module._PROBE_SCRIPT, {})

    [result] = json.loads(capsys.readouterr().out)
    assert result["probe"] == "binary-distribution"
    assert result["status"] == "fail"
    assert expected_summary in result["summary"]
    assert str(tmp_path) not in result["summary"]
    assert drive_path not in result["summary"]
    assert unc_path not in result["summary"]
    if secret_suffix is not None:
        assert secret_suffix not in result["summary"]
        assert secret_suffix not in json.dumps(result["details"])
        assert "<path>" in result["summary"]
    assert result["details"]["error"] == result["summary"]
    assert len(result["summary"]) <= 240
    assert len(result["details"]["error"]) <= 240


def test_behavior_summary_accepts_valid_no_update_and_unchanged_evidence() -> None:
    baseline = _probe(
        "claude-agent-sdk",
        "1.0.0",
        "current-environment",
        "pass",
        {"missing": []},
    )
    no_update = summarize_behavior(
        [baseline],
        [],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    assert no_update["status"] == "pass"
    assert no_update["missing_comparison_count"] == 0

    candidate = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "pass",
        {"missing": []},
    )
    diffs = diff_behavior_results([baseline, candidate])
    unchanged = summarize_behavior(
        [baseline, candidate],
        diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    assert unchanged["status"] == "pass"
    assert unchanged["unchanged_count"] == 1


def test_behavior_summary_reports_changed_only_for_complete_evidence() -> None:
    baseline = _probe(
        "claude-agent-sdk",
        "1.0.0",
        "current-baseline",
        "pass",
        {"missing": [], "required_fields": ["a"]},
    )
    candidate = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "pass",
        {"missing": [], "required_fields": ["a", "b"]},
    )
    diffs = diff_behavior_results([baseline, candidate])

    summary = summarize_behavior(
        [baseline, candidate],
        diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )

    assert summary["status"] == "changed"
    assert summary["changed_count"] == 1


def test_behavior_summary_marks_errors_skips_missing_and_malformed_as_incomplete() -> None:
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    baseline = _probe("claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []})
    error = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "fail",
        {"error": "probe crashed", "failure_step": "probe-execution"},
    )
    skipped = _probe("claude-agent-sdk", "2.0.0", "candidate", "skip", {"reason": "opt-in"})
    wrong_version = _probe("claude-agent-sdk", "3.0.0", "candidate", "pass", {"missing": []})

    probe_error = summarize_behavior(
        [baseline, error],
        [],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    probe_skip = summarize_behavior(
        [baseline, skipped],
        [],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    missing = summarize_behavior(
        [baseline, wrong_version],
        diff_behavior_results([baseline, wrong_version]),
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    malformed = summarize_behavior(
        [{"package": "claude-agent-sdk", "summary": None}],
        [],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )

    assert probe_error["status"] == "incomplete"
    assert probe_error["probe_error_count"] == 1
    assert probe_skip["status"] == "incomplete"
    assert probe_skip["skipped_count"] == 1
    assert missing["status"] == "incomplete"
    assert missing["missing_comparison_count"] == 1
    assert malformed["status"] == "incomplete"
    assert malformed["malformed_count"] == 1


def test_behavior_summary_contains_malformed_nested_contract_details() -> None:
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    baseline = _probe(
        "claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []}
    )
    malformed_candidate = BehaviorProbeResult(
        package="claude-agent-sdk",
        version="2.0.0",
        scope="candidate",
        probe="adapter-contract",
        status="fail",
        summary="missing fields",
        details={"missing": 7},
    )
    supplied_diffs = diff_behavior_results([baseline, malformed_candidate])

    summary = summarize_behavior(
        [baseline, malformed_candidate],
        supplied_diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )

    assert summary["status"] == "incomplete"
    assert summary["malformed_count"] == 2
    assert "details.missing" in " ".join(summary["reasons"])


def test_behavior_summary_treats_missing_fields_plus_error_as_contract_failure() -> None:
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    baseline = _probe(
        "claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []}
    )
    candidate = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "fail",
        {"missing": ["required_field"], "error": "secondary diagnostic"},
    )
    diffs = diff_behavior_results([baseline, candidate])

    summary = summarize_behavior(
        [baseline, candidate],
        diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )

    assert diffs[0].severity == "breaking"
    assert summary["status"] == "fail"
    assert summary["contract_failure_count"] == 1
    assert summary["probe_error_count"] == 0


def test_behavior_summary_requires_complete_probe_sets_for_transition() -> None:
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    results = [
        _probe(
            "claude-agent-sdk",
            "1.0.0",
            "current-baseline",
            "pass",
            {"missing": []},
        ),
        BehaviorProbeResult(
            package="claude-agent-sdk",
            version="1.0.0",
            scope="current-baseline",
            probe="package-import",
            status="pass",
            summary="imported",
            details={},
        ),
        _probe(
            "claude-agent-sdk",
            "2.0.0",
            "candidate",
            "pass",
            {"missing": []},
        ),
    ]
    diffs = diff_behavior_results(results)

    summary = summarize_behavior(
        results,
        diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )

    assert {diff.probe for diff in diffs} == {"adapter-contract"}
    assert summary["status"] == "incomplete"
    assert summary["missing_comparison_count"] == 1


def test_behavior_summary_status_precedence_is_fail_then_incomplete_then_changed() -> None:
    baseline = _probe("claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []})
    contract_failure = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "fail",
        {"missing": ["required_field"]},
    )
    extra_skip = _probe("fake-sdk", "1.0.0", "candidate", "skip", {"reason": "missing"})
    failed = summarize_behavior(
        [baseline, contract_failure, extra_skip],
        diff_behavior_results([baseline, contract_failure, extra_skip]),
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    assert failed["status"] == "fail"
    assert failed["contract_failure_count"] == 1
    assert failed["skipped_count"] == 1

    changed_candidate = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "pass",
        {"missing": [], "required_fields": ["new"]},
    )
    changed_diffs = diff_behavior_results([baseline, changed_candidate])
    incomplete = summarize_behavior(
        [baseline, changed_candidate, extra_skip],
        changed_diffs,
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")],
        expected_baselines={"claude-agent-sdk": "1.0.0"},
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["changed_count"] == 1


def test_behavior_probe_guard_blocks_complete_breaking_candidate_payload() -> None:
    baseline = _probe(
        "google-antigravity", "1.0.0", "current-baseline", "pass", {"missing": []}
    )
    candidate = _probe(
        "google-antigravity",
        "2.0.0",
        "candidate",
        "fail",
        {"missing": ["required_field"]},
    )
    transition = ResolverTransition("google-antigravity", "1.0.0", "2.0.0")
    behavior = _behavior_payload(
        [baseline, candidate],
        expected_packages=["google-antigravity"],
        expected_transitions=[transition],
    )
    guarded = with_behavior_probe_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        _sdk_evidence(
            package="google-antigravity",
            locked_version="1.0.0",
            installed_version="1.0.0",
            candidate_version="2.0.0",
        ),
        behavior,
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True
    assert behavior["summary"]["status"] == "fail"
    assert "breaking" in " ".join(guarded["findings"][-1]["evidence"])


def test_behavior_probe_guard_allows_valid_pass_and_changed_evidence() -> None:
    architecture = {
        "findings": [],
        "safe_to_implement": True,
        "manual_design_required": False,
        "uncertainty": [],
    }
    baseline = _probe("claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []})
    pass_payload = _behavior_payload(
        [baseline],
        expected_packages=["claude-agent-sdk"],
    )
    changed_candidate = _probe(
        "claude-agent-sdk",
        "2.0.0",
        "candidate",
        "pass",
        {"missing": [], "required_fields": ["new"]},
    )
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    changed_payload = _behavior_payload(
        [baseline, changed_candidate],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
    )

    assert (
        with_behavior_probe_guard(architecture, _sdk_evidence(), pass_payload)[
            "safe_to_implement"
        ]
        is True
    )
    assert (
        with_behavior_probe_guard(
            architecture,
            _sdk_evidence(candidate_version="2.0.0"),
            changed_payload,
        )["safe_to_implement"]
        is True
    )


def test_behavior_probe_guard_blocks_self_declared_empty_expectations() -> None:
    behavior = _behavior_payload([], expected_packages=[])
    assert behavior["summary"]["status"] == "pass"

    guarded = with_behavior_probe_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        _sdk_evidence(),
        behavior,
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True
    assert "contradicts deterministic evidence" in " ".join(
        guarded["findings"][-1]["evidence"]
    )


def test_behavior_probe_guard_blocks_failed_incomplete_and_invalid_evidence() -> None:
    architecture = {
        "findings": [],
        "safe_to_implement": True,
        "manual_design_required": False,
        "uncertainty": [],
    }
    baseline = _probe("claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []})
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    failed_payload = _behavior_payload(
        [
            baseline,
            _probe(
                "claude-agent-sdk",
                "2.0.0",
                "candidate",
                "fail",
                {"missing": ["required_field"]},
            ),
        ],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
    )
    incomplete_payload = _behavior_payload(
        [
            baseline,
            _probe(
                "claude-agent-sdk",
                "2.0.0",
                "candidate",
                "skip",
                {"reason": "opt-in"},
            ),
        ],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
    )
    missing_summary = _behavior_payload(
        [baseline],
        expected_packages=["claude-agent-sdk"],
    )
    missing_summary.pop("summary")
    unknown_status = _behavior_payload(
        [baseline],
        expected_packages=["claude-agent-sdk"],
    )
    unknown_status["summary"]["status"] = "mystery"
    contradictory = _behavior_payload(
        [baseline],
        expected_packages=["claude-agent-sdk"],
    )
    contradictory["results"] = failed_payload["results"]
    contradictory["expected_transitions"] = failed_payload["expected_transitions"]
    malformed = _behavior_payload(
        [baseline],
        expected_packages=["claude-agent-sdk"],
    )
    malformed["results"] = [{"package": "claude-agent-sdk", "summary": None}]

    for evidence, payload in (
        (_sdk_evidence(candidate_version="2.0.0"), failed_payload),
        (_sdk_evidence(candidate_version="2.0.0"), incomplete_payload),
        (_sdk_evidence(), missing_summary),
        (_sdk_evidence(), unknown_status),
        (_sdk_evidence(), contradictory),
        (_sdk_evidence(), malformed),
    ):
        guarded = with_behavior_probe_guard(architecture, evidence, payload)
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
    (report_root / "behavior_summary.json").write_text('{"status":"pass"}', encoding="utf-8")
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
    assert "reports/sdk-evolution/run-1/behavior_summary.json" in paths
    assert "reports/sdk-evolution/run-1/api_snapshots/01-claude-agent-sdk.json" in paths
    assert state["artifacts"]["behavior_summary.json"]["sha256"]
    assert all(not path.startswith("/") for path in paths)
    assert all("/private/tmp" not in path and "/tmp/" not in path for path in paths)


def test_report_exposes_snapshot_and_behavior_evidence_failures() -> None:
    behavior = _behavior_payload(
        [
            BehaviorProbeResult(
                package="claude-agent-sdk",
                version="2.0.0",
                scope="candidate",
                probe="adapter-contract",
                status="fail",
                summary="probe crashed",
                details={"error": "probe crashed", "failure_step": "probe-execution"},
            )
        ],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")],
    )

    report = render_markdown_report(
        config={"runtime": "fake", "implementation_enabled": False, "draft_pr": False},
        evidence=_sdk_evidence(candidate_version="2.0.0"),
        snapshots=[
            {
                "package": "claude-agent-sdk",
                "version": "2.0.0",
                "source": "isolated-venv",
                "import_error": "candidate import failed\nwith details",
            }
        ],
        api_diffs=[],
        release_notes=[],
        behavior=behavior,
        current_state={"promotion": {"status": "skipped", "promoted": False}},
        direction={},
        architecture={
            "manual_design_required": True,
            "recursive_self_adaptation_impact": False,
            "safe_to_implement": False,
        },
        implementation={},
        review={},
    )

    assert "## API Snapshots" in report
    assert "- Status: `incomplete`" in report
    assert "- Snapshot count: `1`" in report
    assert "- Import or execution errors: `1`" in report
    assert "candidate import failed with details" in report
    assert "## Behavior Probes" in report
    assert "- Probe errors: `1`" in report
    assert "- Missing comparisons: `1`" in report
    assert "probe crashed" in report


def test_report_persists_and_renders_recomputed_behavior_summary(tmp_path: Path) -> None:
    baseline = _probe(
        "claude-agent-sdk", "1.0.0", "current-baseline", "pass", {"missing": []}
    )
    transition = ResolverTransition("claude-agent-sdk", "1.0.0", "2.0.0")
    behavior = _behavior_payload(
        [
            baseline,
            _probe(
                "claude-agent-sdk",
                "2.0.0",
                "candidate",
                "fail",
                {"missing": ["required_field"]},
            ),
        ],
        expected_packages=["claude-agent-sdk"],
        expected_transitions=[transition],
    )
    behavior["summary"] = _behavior_payload(
        [baseline], expected_packages=["claude-agent-sdk"]
    )["summary"]
    assert behavior["summary"]["status"] == "pass"

    report_root = tmp_path / "reports" / "run-1"
    context = RunContext(
        run_id="run-1",
        workspace=tmp_path,
        report_root=report_root,
        runtime="fake",
        event_log_path=report_root / "events.jsonl",
        implementation_enabled=False,
        draft_pr=False,
    )
    report_path = write_run_report(
        context,
        config={"runtime": "fake", "implementation_enabled": False, "draft_pr": False},
        evidence=_sdk_evidence(candidate_version="2.0.0"),
        snapshots=[],
        api_diffs=[],
        release_notes=[],
        behavior=behavior,
        current_state={"promotion": {"status": "skipped", "promoted": False}},
        direction={},
        architecture={
            "manual_design_required": True,
            "recursive_self_adaptation_impact": False,
            "safe_to_implement": False,
        },
        implementation={},
        review={},
    )

    persisted = json.loads(
        (report_root / "behavior_summary.json").read_text(encoding="utf-8")
    )
    report = report_path.read_text(encoding="utf-8")
    assert persisted["status"] == "fail"
    assert persisted["contract_failure_count"] == 1
    assert "- Status: `fail`" in report
    assert "failed the required contract" in report


def test_snapshot_module_mapping_preserves_distribution_identities() -> None:
    expected = {
        "claude-agent-sdk": "claude_agent_sdk",
        "openai-codex": "openai_codex",
        "openai-codex-cli-bin": "codex_cli_bin",
        "google-antigravity": "google.antigravity",
    }

    assert {package: DEFAULT_MODULES[package] for package in expected} == expected


def test_current_codex_cli_snapshot_uses_real_import_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = types.ModuleType("codex_cli_bin")
    module.bundled_codex_path = lambda: Path("unused")
    monkeypatch.setitem(sys.modules, "codex_cli_bin", module)

    snapshot = snapshot_current_api("openai-codex-cli-bin", version="1.2.3")

    assert snapshot.package == "openai-codex-cli-bin"
    assert snapshot.module == "codex_cli_bin"
    assert snapshot.import_error is None


def test_isolated_codex_cli_snapshot_receives_real_import_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[Any, ...]] = []

    def fake_run(args: Any, **kwargs: Any) -> Any:
        del kwargs
        calls.append(tuple(args))
        payload = (
            '{"package":"openai-codex-cli-bin","version":"1.2.3",'
            '"module":"codex_cli_bin","members":[],"import_error":null}'
        )
        return types.SimpleNamespace(
            stdout=payload if len(calls) == 3 else "",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("examples.sdk_evolution_agent.snapshots.subprocess.run", fake_run)

    snapshot = snapshot_candidate_in_venv("openai-codex-cli-bin", "1.2.3")

    assert calls[1][-1] == "openai-codex-cli-bin==1.2.3"
    assert calls[2][-1] == "codex_cli_bin"
    assert snapshot.package == "openai-codex-cli-bin"
    assert snapshot.module == "codex_cli_bin"
    assert snapshot.import_error is None


def test_real_codex_cli_snapshot_and_probe_when_extra_is_installed() -> None:
    pytest.importorskip("codex_cli_bin", reason="Codex extra is not installed")

    snapshot = snapshot_current_api("openai-codex-cli-bin")
    (probe,) = probe_current_package("openai-codex-cli-bin", version=snapshot.version)

    assert snapshot.module == "codex_cli_bin"
    assert snapshot.import_error is None
    assert probe.probe == "binary-distribution"
    assert probe.status == "pass"
    assert probe.details["bundled_binary"] == "regular-file"


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


@pytest.mark.parametrize(
    ("failure_call", "detail"),
    [
        (1, "venv creation failed"),
        (2, "package install failed"),
        (3, "snapshot execution failed"),
    ],
)
def test_candidate_snapshot_records_subprocess_failures(
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
    detail: str,
) -> None:
    calls = 0

    def fake_run(args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=args,
                stderr=(detail + " ") * 100,
            )
        return types.SimpleNamespace(stdout="{}", stderr="", returncode=0)

    monkeypatch.setattr("examples.sdk_evolution_agent.snapshots.subprocess.run", fake_run)

    snapshot = snapshot_candidate_in_venv("claude-agent-sdk", "0.2.110")

    assert snapshot.package == "claude-agent-sdk"
    assert snapshot.version == "0.2.110"
    assert snapshot.source == "isolated-venv"
    assert snapshot.import_error is not None
    assert detail in snapshot.import_error
    assert len(snapshot.import_error) <= 600


def test_candidate_snapshot_records_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(args: Any, **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])

    monkeypatch.setattr("examples.sdk_evolution_agent.snapshots.subprocess.run", fake_run)

    snapshot = snapshot_candidate_in_venv("claude-agent-sdk", "0.2.110", timeout=7)

    assert snapshot.import_error is not None
    assert "timed out after 7s" in snapshot.import_error
    assert snapshot.source == "isolated-venv"


def test_candidate_snapshot_records_malformed_output(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def fake_run(args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return types.SimpleNamespace(
            stdout="not-json" if calls == 3 else "",
            stderr="",
            returncode=0,
        )

    monkeypatch.setattr("examples.sdk_evolution_agent.snapshots.subprocess.run", fake_run)

    snapshot = snapshot_candidate_in_venv("claude-agent-sdk", "0.2.110")

    assert snapshot.import_error is not None
    assert "malformed snapshot output" in snapshot.import_error
    assert snapshot.source == "isolated-venv"


def test_candidate_snapshot_scrubs_subprocess_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_envs: list[dict[str, str] | None] = []
    calls = 0

    def fake_run(args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        captured_envs.append(kwargs.get("env"))
        payload = (
            '{"package":"claude-agent-sdk","version":"0.2.110",'
            '"module":"claude_agent_sdk","members":[],"import_error":null}'
        )
        return types.SimpleNamespace(
            stdout=payload if calls == 3 else "",
            stderr="",
            returncode=0,
        )

    monkeypatch.setenv("ARK_FAKE_SECRET", "not-for-snapshots")
    monkeypatch.setattr("examples.sdk_evolution_agent.snapshots.subprocess.run", fake_run)

    snapshot = snapshot_candidate_in_venv("claude-agent-sdk", "0.2.110")

    assert snapshot.import_error is None
    assert len(captured_envs) == 3
    for env in captured_envs:
        assert env is not None
        assert "ARK_FAKE_SECRET" not in env
        assert env.get("HOME") != os.environ.get("HOME")


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


def test_collect_snapshots_uses_locked_baseline_when_sdk_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def current_snapshot(package: str, *, version: str | None = None) -> ApiSnapshot:
        raise AssertionError(f"ambient snapshot must not represent {package} {version}")

    def isolated_snapshot(package: str, version: str) -> ApiSnapshot:
        calls.append(("isolated", package, version))
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
        isolated_snapshot,
    )

    _collect_snapshots(
        {
            "packages": [
                {
                    "name": "claude-agent-sdk",
                    "locked_version": "0.2.106",
                    "installed_version": None,
                    "latest_version": "0.2.110",
                },
            ],
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update claude-agent-sdk v0.2.106 -> v0.2.110\n",
            },
        },
        inspect_candidates=True,
    )

    assert calls == [
        ("isolated", "claude-agent-sdk", "0.2.106"),
        ("isolated", "claude-agent-sdk", "0.2.110"),
    ]


def test_collect_snapshots_without_opt_in_never_installs_even_when_lock_drifted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The security default: a drifted lock (locked != installed) plus pending
    # updates must NOT trigger isolated-venv installs unless --inspect-candidates
    # was passed; everything snapshots from the already-installed environment.
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
        inspect_candidates=False,
    )

    assert calls == [("current", "claude-agent-sdk", "0.2.106")]


def test_collect_snapshots_without_opt_in_reports_missing_ambient_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str | None]] = []

    def current_snapshot(package: str, *, version: str | None = None) -> ApiSnapshot:
        calls.append(("current", package, version))
        return ApiSnapshot(
            package=package,
            version=version,
            module=package.replace("-", "_"),
            import_error="not installed",
        )

    def isolated_snapshot(package: str, version: str) -> ApiSnapshot:
        raise AssertionError(f"isolated snapshot must not run for {package} {version}")

    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_current_api",
        current_snapshot,
    )
    monkeypatch.setattr(
        "examples.sdk_evolution_agent.cli.snapshot_candidate_in_venv",
        isolated_snapshot,
    )

    snapshots = _collect_snapshots(
        {
            "packages": [
                {
                    "name": "claude-agent-sdk",
                    "locked_version": "0.2.106",
                    "installed_version": None,
                    "latest_version": "0.2.110",
                }
            ],
            "refresh_preview": {
                "stdout": "",
                "stderr": "Update claude-agent-sdk v0.2.106 -> v0.2.110\n",
            },
        },
        inspect_candidates=False,
    )

    assert calls == [("current", "claude-agent-sdk", None)]
    assert snapshots[0].import_error == "not installed"


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
    assert (
        guarded["findings"][-1]["evidence"][0]
        == "missing api_diffs for google-antigravity 0.1.2 -> 0.1.4"
    )


@pytest.mark.parametrize(
    "api_diff",
    [
        {
            "package": "google-antigravity",
            "from_version": "0.1.1",
            "to_version": "0.1.4",
        },
        {
            "package": "google-antigravity",
            "from_version": "0.1.2",
            "to_version": "0.1.5",
        },
        {
            "package": "google-antigravity-extra",
            "from_version": "0.1.2",
            "to_version": "0.1.4",
        },
    ],
)
def test_candidate_api_diff_guard_requires_exact_transition(api_diff: dict[str, str]) -> None:
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
        [api_diff],
    )

    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


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
    assert (report_path.parent / "behavior_summary.json").exists()
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
        lambda packages, updates, *, inspect_candidates=False, expected_transitions=None: (
            _behavior_payload(
                [
                    _probe(
                        "claude-agent-sdk",
                        None,
                        "current-environment",
                        "pass",
                        {},
                    )
                ],
                expected_packages=["claude-agent-sdk"],
                expected_transitions=expected_transitions,
            )
        ),
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


def test_finalize_report_skips_when_report_dir_is_outside_the_repo(tmp_path: Path) -> None:
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
        # check-ignore exits 128 when it cannot judge the path (outside the repo).
        if command[:2] == ("git", "check-ignore"):
            return CommandResult(command=command, returncode=128, stderr="fatal: outside repo")
        return CommandResult(command=command, returncode=0)

    report_path = tmp_path / "elsewhere" / "run" / "report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("x", encoding="utf-8")

    # A --report-dir outside the workspace must skip cleanly, not stage a
    # doomed out-of-repo path and raise.
    _commit_final_autonomous_pr_report(
        tmp_path / "repo",
        report_path=report_path,
        options=RunOptions(workspace=tmp_path / "repo", runtime="fake"),
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
        lambda packages, updates, *, inspect_candidates=False, expected_transitions=None: (
            _behavior_payload(
                [
                    _probe(
                        "claude-agent-sdk",
                        "0.2.1",
                        "current-environment",
                        "pass",
                        {"missing": []},
                    ),
                    _probe(
                        "claude-agent-sdk",
                        "0.3.0",
                        "candidate",
                        "pass",
                        {"missing": []},
                    ),
                ],
                expected_packages=["claude-agent-sdk"],
                expected_transitions=expected_transitions,
            )
        ),
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

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> RecordingRuntime:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object, /) -> None:
        await self.aclose()


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
    version: str | None,
    scope: str,
    status: str,
    details: dict[str, Any],
) -> BehaviorProbeResult:
    return BehaviorProbeResult(
        package=package,
        version=version,
        scope=scope,
        probe="adapter-contract",
        status=status,
        summary=status,
        details=details,
    )


def _behavior_payload(
    results: list[BehaviorProbeResult],
    *,
    diffs: list[BehaviorDiff] | None = None,
    expected_packages: list[str] | None = None,
    expected_transitions: list[ResolverTransition] | tuple[ResolverTransition, ...] | None = None,
    expected_baselines: dict[str, str | None] | None = None,
) -> dict[str, Any]:
    observed_diffs = diffs if diffs is not None else list(diff_behavior_results(results))
    packages = expected_packages or []
    transitions = list(expected_transitions or [])
    baselines = dict(expected_baselines or {})
    for package in packages:
        transition = next(
            (item for item in transitions if item.package == package),
            None,
        )
        if transition is not None:
            baselines.setdefault(package, transition.from_version)
            continue
        observation = next(
            (
                result
                for result in results
                if result.package == package
                and result.scope in {"current-baseline", "current-environment"}
            ),
            None,
        )
        baselines.setdefault(package, observation.version if observation is not None else None)
    payload: dict[str, Any] = to_jsonable(
        {
            "results": results,
            "diffs": observed_diffs,
            "expected_packages": packages,
            "expected_transitions": transitions,
            "expected_baselines": baselines,
        }
    )
    payload["summary"] = assess_behavior_payload(payload)
    return payload


def _sdk_evidence(
    *,
    package: str = "claude-agent-sdk",
    locked_version: str | None = "1.0.0",
    installed_version: str | None = "1.0.0",
    candidate_version: str | None = None,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "packages": [
            {
                "name": package,
                "locked_version": locked_version,
                "installed_version": installed_version,
            }
        ]
    }
    if candidate_version is not None:
        evidence["refresh_preview"] = {
            "stdout": "",
            "stderr": (
                f"Update {package} v{locked_version or installed_version} "
                f"-> v{candidate_version}\n"
            ),
        }
    return evidence


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
