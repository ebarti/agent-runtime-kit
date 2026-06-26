from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.sdk_evolution_agent.models import DEFAULT_PACKAGES
from scripts.sdk_evolution_upgrade import (
    UpgradeConfig,
    build_agent_command,
    cutoff_free_env,
    run_upgrade,
)


def test_upgrade_script_builds_all_package_report_command(tmp_path: Path) -> None:
    config = UpgradeConfig(repo_root=tmp_path, runtime="codex-agent-sdk")

    command = build_agent_command(
        config,
        implementation_enabled=False,
        branch_name="sdk-evolution-upgrade-test",
    )

    assert "--implementation-enabled" not in command
    assert "--create-branch" not in command
    assert command[:9] == (
        "uv",
        "run",
        "--extra",
        "codex",
        "python",
        "-m",
        "examples.sdk_evolution_agent",
        "--runtime",
        "codex-agent-sdk",
    )
    for package in DEFAULT_PACKAGES:
        assert _has_package(command, package)


def test_upgrade_script_builds_implementation_without_inner_branch_creation(
    tmp_path: Path,
) -> None:
    config = UpgradeConfig(
        repo_root=tmp_path,
        runtime="codex-agent-sdk",
        draft_pr=True,
        pr_base="main",
    )

    command = build_agent_command(
        config,
        implementation_enabled=True,
        branch_name="sdk-evolution-upgrade-test",
    )

    assert "--implementation-enabled" in command
    assert "--draft-pr" in command
    assert "--branch-name" in command
    assert "sdk-evolution-upgrade-test" in command
    assert "--create-branch" not in command
    for package in DEFAULT_PACKAGES:
        assert _has_package(command, package)


def test_upgrade_script_creates_unique_outer_worktree_and_runs_all_phases(
    tmp_path: Path,
) -> None:
    calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def runner(command: tuple[str, ...], *, cwd: Path, env: dict[str, str]) -> int:
        calls.append((command, cwd, env))
        return 0

    config = UpgradeConfig(
        repo_root=tmp_path / "repo",
        runtime="codex-agent-sdk",
        worktree_parent=tmp_path,
        branch_prefix="sdk-evolution-upgrade-test",
    )

    run = run_upgrade(config, command_runner=runner)

    assert run.branch_name.startswith("sdk-evolution-upgrade-test-")
    assert run.worktree_path == tmp_path / run.branch_name
    assert calls[0][0] == ("git", "fetch", "origin", "--prune")
    assert calls[1][0][:4] == ("git", "worktree", "add", "-b")
    assert calls[1][0][4] == run.branch_name
    assert calls[1][0][5] == str(run.worktree_path)
    assert ("uv", "run", "--extra", "codex", "python") in {call[0][:5] for call in calls}
    implementation_commands = [
        command for command, _cwd, _env in calls if "--implementation-enabled" in command
    ]
    assert len(implementation_commands) == 1
    assert "--create-branch" not in implementation_commands[0]
    for package in DEFAULT_PACKAGES:
        assert _has_package(implementation_commands[0], package)


def test_upgrade_script_report_only_skips_pr_auth_and_implementation(tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []

    def runner(command: tuple[str, ...], **_kwargs: Any) -> int:
        calls.append(command)
        return 0

    run_upgrade(
        UpgradeConfig(
            repo_root=tmp_path / "repo",
            runtime="claude-agent-sdk",
            worktree_parent=tmp_path,
            report_only=True,
        ),
        command_runner=runner,
    )

    assert ("gh", "auth", "status") not in calls
    assert not any("--implementation-enabled" in command for command in calls)
    assert not any("ensure-codex" in command for command in calls)


def test_upgrade_script_removes_uv_freshness_cutoffs() -> None:
    env = cutoff_free_env(
        {
            "UV_EXCLUDE_NEWER": "2026-01-01",
            "UV_EXCLUDE_NEWER_PACKAGE_OPENAI_CODEX": "2026-01-01",
            "CUSTOM_EXCLUDE_NEWER": "2026-01-01",
            "VIRTUAL_ENV": "/tmp/old-worktree/.venv",
            "KEEP": "1",
        }
    )

    assert env == {"KEEP": "1"}


def _has_package(command: tuple[str, ...], package: str) -> bool:
    return any(
        current == "--package" and index + 1 < len(command) and command[index + 1] == package
        for index, current in enumerate(command)
    )
