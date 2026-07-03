from __future__ import annotations

from pathlib import Path

from examples.sdk_evolution_agent.models import CommandResult, RunOptions
from examples.sdk_evolution_agent.preflight import validate_run_plan


def _runner(
    command: tuple[str, ...],
    *,
    cwd: Path | None = None,
) -> CommandResult:
    del cwd
    if command == ("git", "branch", "--show-current"):
        return CommandResult(command=command, returncode=0, stdout="main\n")
    if command == ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"):
        return CommandResult(command=command, returncode=0, stdout="origin/main\n")
    if command == ("git", "status", "--porcelain"):
        return CommandResult(command=command, returncode=0, stdout="")
    return CommandResult(command=command, returncode=0)


def test_preflight_reports_all_violations_at_once(tmp_path: Path) -> None:
    violations = validate_run_plan(
        RunOptions(
            workspace=tmp_path,
            draft_pr=True,
            implementation_enabled=False,
            create_branch=True,
        ),
        tmp_path,
        command_runner=_runner,
    )

    assert "--draft-pr requires --implementation-enabled" in violations
    assert "--create-branch requires --branch-name" in violations
    assert "--draft-pr requires a non-detached head branch" in violations


def test_draft_pr_refused_on_default_branch(tmp_path: Path) -> None:
    violations = validate_run_plan(
        RunOptions(
            workspace=tmp_path,
            draft_pr=True,
            implementation_enabled=True,
            refresh_preview=True,
        ),
        tmp_path,
        command_runner=_runner,
    )

    assert "--draft-pr refuses to push the default branch (main)" in violations


def test_allow_dirty_overrides_clean_check(tmp_path: Path) -> None:
    def dirty_runner(command: tuple[str, ...], *, cwd: Path | None = None) -> CommandResult:
        del cwd
        if command == ("git", "status", "--porcelain"):
            return CommandResult(command=command, returncode=0, stdout=" M uv.lock\n")
        return _runner(command)

    assert validate_run_plan(
        RunOptions(
            workspace=tmp_path,
            implementation_enabled=True,
            refresh_preview=True,
            allow_dirty=True,
        ),
        tmp_path,
        command_runner=dirty_runner,
    ) == ()


def test_allow_cap_raise_requires_candidate_inspection(tmp_path: Path) -> None:
    violations = validate_run_plan(
        RunOptions(
            workspace=tmp_path,
            implementation_enabled=True,
            refresh_preview=True,
            allow_cap_raise=True,
        ),
        tmp_path,
        command_runner=_runner,
    )

    assert "--allow-cap-raise requires --inspect-candidates" in violations
