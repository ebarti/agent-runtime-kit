"""Preflight validation for SDK evolution runs."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from examples.sdk_evolution_agent.collectors import CommandRunner, run_command
from examples.sdk_evolution_agent.models import RunOptions


class PreflightError(RuntimeError):
    """Raised when a run configuration is unsafe before any side effect."""

    def __init__(self, violations: tuple[str, ...]) -> None:
        self.violations = violations
        message = "SDK evolution preflight failed:\n" + "\n".join(
            f"- {violation}" for violation in violations
        )
        super().__init__(message)


def validate_run_plan(
    options: RunOptions,
    workspace: Path,
    *,
    command_runner: CommandRunner | None = None,
) -> tuple[str, ...]:
    """Return human-readable run-plan violations."""

    runner = command_runner or run_command
    violations: list[str] = []

    if options.draft_pr and not options.implementation_enabled:
        violations.append("--draft-pr requires --implementation-enabled")
    if options.implementation_enabled and not options.refresh_preview:
        violations.append("--implementation-enabled requires --refresh-preview")
    if options.create_branch and not options.branch_name:
        violations.append("--create-branch requires --branch-name")
    if options.allow_cap_raise and not options.implementation_enabled:
        violations.append("--allow-cap-raise requires --implementation-enabled")
    if options.allow_cap_raise and not options.inspect_candidates:
        violations.append("--allow-cap-raise requires --inspect-candidates")

    if options.draft_pr:
        default_branch = _default_branch(workspace, runner)
        head_branch = (
            options.branch_name
            if options.create_branch
            else _git_stdout(workspace, runner, ("git", "branch", "--show-current"))
        )
        if not head_branch:
            violations.append("--draft-pr requires a non-detached head branch")
        elif head_branch == default_branch:
            violations.append(f"--draft-pr refuses to push the default branch ({default_branch})")

    if options.implementation_enabled and not options.allow_dirty:
        status = _git_stdout(workspace, runner, ("git", "status", "--porcelain"))
        if status:
            violations.append("--implementation-enabled requires a clean git worktree")

    return tuple(violations)


def _default_branch(root: Path, runner: Callable[..., object]) -> str:
    raw = _git_stdout(root, runner, ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"))
    if raw.startswith("origin/"):
        return raw.removeprefix("origin/")
    return raw or "main"


def _git_stdout(root: Path, runner: Callable[..., object], command: tuple[str, ...]) -> str:
    result = runner(command, cwd=root)
    if getattr(result, "returncode", 1) != 0:
        return ""
    return str(getattr(result, "stdout", "")).strip()
