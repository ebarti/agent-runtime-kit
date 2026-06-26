from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime_kit.sdk_evolution_upgrade import find_repo_root
from examples.sdk_evolution_agent.models import DEFAULT_PACKAGES
from scripts.sdk_evolution_upgrade import (
    DEFAULT_BASE,
    DEFAULT_RUNTIME,
    UpgradeConfig,
    build_agent_command,
    cutoff_free_env,
    parse_args,
    run_upgrade,
)


def test_named_upgrade_command_is_declared() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"

    assert (
        'sdk-evolution-upgrade = "agent_runtime_kit.sdk_evolution_upgrade:main"'
        in pyproject.read_text(encoding="utf-8")
    )


def test_named_upgrade_entrypoint_finds_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    assert find_repo_root(repo_root / "tests") == repo_root


def test_upgrade_script_defaults_to_full_codex_upgrade_from_main() -> None:
    config = parse_args([])

    assert config.runtime == DEFAULT_RUNTIME
    assert config.base == DEFAULT_BASE
    assert config.report_only is False
    assert config.draft_pr is True
    assert config.branch_prefix == "sdk-evolution-upgrade"


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
