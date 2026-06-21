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


def create_draft_pr(
    root: Path,
    *,
    title: str,
    body: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Open a draft PR with gh when authenticated."""

    command_runner = command_runner or run_command
    return command_runner(
        ("gh", "pr", "create", "--draft", "--title", title, "--body", body),
        cwd=root,
    )
