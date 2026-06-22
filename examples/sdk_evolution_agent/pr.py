"""Optional draft pull-request helpers."""

from __future__ import annotations

from pathlib import Path

from examples.sdk_evolution_agent.collectors import CommandRunner, run_command
from examples.sdk_evolution_agent.models import CommandResult


def build_draft_pr_body(report_markdown: str) -> str:
    """Build a draft PR body from a local report."""

    return "\n".join(
        [
            "## SDK Evolution Report",
            "",
            report_markdown,
            "",
            "## Safety",
            "",
            "- Draft PR only.",
            "- No auto-merge.",
            "- Local credentials are not scraped.",
        ]
    )


def create_branch(
    root: Path,
    branch_name: str,
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Create or switch to a local branch."""

    command_runner = command_runner or run_command
    return command_runner(("git", "switch", "-c", branch_name), cwd=root)


def stage_paths(
    root: Path,
    paths: tuple[str, ...],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Stage paths for an autonomous SDK update PR."""

    command_runner = command_runner or run_command
    return command_runner(("git", "add", *paths), cwd=root)


def commit_staged(
    root: Path,
    *,
    message: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Commit staged SDK update artifacts."""

    command_runner = command_runner or run_command
    return command_runner(("git", "commit", "-m", message), cwd=root)


def push_branch(
    root: Path,
    *,
    branch_name: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Push the current SDK update branch."""

    command_runner = command_runner or run_command
    return command_runner(("git", "push", "-u", "origin", branch_name), cwd=root)


def amend_last_commit(
    root: Path,
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Amend the current commit without changing its message."""

    command_runner = command_runner or run_command
    return command_runner(("git", "commit", "--amend", "--no-edit"), cwd=root)


def force_push_branch(
    root: Path,
    *,
    branch_name: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Update the autonomous PR branch after amending its report commit."""

    command_runner = command_runner or run_command
    return command_runner(
        ("git", "push", "--force-with-lease", "origin", branch_name),
        cwd=root,
    )


def create_draft_pr(
    root: Path,
    *,
    title: str,
    body: str,
    base: str | None = None,
    head: str | None = None,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Open a draft PR with gh when authenticated."""

    command_runner = command_runner or run_command
    command = ["gh", "pr", "create", "--draft", "--title", title, "--body", body]
    if base:
        command.extend(("--base", base))
    if head:
        command.extend(("--head", head))
    return command_runner(tuple(command), cwd=root)
