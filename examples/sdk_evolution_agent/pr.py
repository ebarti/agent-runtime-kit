"""Optional draft pull-request helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.collectors import CommandRunner, _call_runner, run_command
from examples.sdk_evolution_agent.models import CommandResult

SDK_EVOLUTION_MARKER_PREFIX = "<!-- sdk-evolution:"
SDK_EVOLUTION_MARKER_SUFFIX = "-->"


def build_draft_pr_body(
    report_markdown: str,
    *,
    package_versions: dict[str, str] | None = None,
    run_id: str | None = None,
) -> str:
    """Build a draft PR body from a local report."""

    marker = build_pr_marker(package_versions or {}, run_id=run_id or "")
    return "\n".join(
        [
            marker,
            "",
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


def build_pr_marker(package_versions: dict[str, str], *, run_id: str) -> str:
    payload = {"packages": dict(sorted(package_versions.items())), "run_id": run_id}
    marker_json = json.dumps(payload, sort_keys=True)
    return f"{SDK_EVOLUTION_MARKER_PREFIX} {marker_json} {SDK_EVOLUTION_MARKER_SUFFIX}"


def parse_pr_marker(body: str) -> dict[str, Any] | None:
    start = body.find(SDK_EVOLUTION_MARKER_PREFIX)
    if start < 0:
        return None
    start += len(SDK_EVOLUTION_MARKER_PREFIX)
    end = body.find(SDK_EVOLUTION_MARKER_SUFFIX, start)
    if end < 0:
        return None
    try:
        payload = json.loads(body[start:end].strip())
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def create_branch(
    root: Path,
    branch_name: str,
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Create or switch to a local branch."""

    command_runner = command_runner or run_command
    return _call_runner(command_runner, ("git", "switch", "-c", branch_name), cwd=root, timeout=120)


def stage_paths(
    root: Path,
    paths: tuple[str, ...],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Stage paths for an autonomous SDK update PR."""

    command_runner = command_runner or run_command
    return _call_runner(command_runner, ("git", "add", *paths), cwd=root, timeout=120)


def commit_staged(
    root: Path,
    *,
    message: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Commit staged SDK update artifacts."""

    command_runner = command_runner or run_command
    return _call_runner(command_runner, ("git", "commit", "-m", message), cwd=root, timeout=120)


def push_branch(
    root: Path,
    *,
    branch_name: str,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Push the current SDK update branch."""

    command_runner = command_runner or run_command
    return _call_runner(
        command_runner,
        ("git", "push", "-u", "origin", branch_name),
        cwd=root,
        timeout=120,
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
    return _call_runner(command_runner, tuple(command), cwd=root, timeout=120)


def list_open_sdk_evolution_prs(
    root: Path, *, command_runner: CommandRunner | None = None
) -> tuple[dict[str, Any], ...]:
    """List open SDK evolution PRs visible to gh."""

    command_runner = command_runner or run_command
    result = _call_runner(
        command_runner,
        (
            "gh",
            "pr",
            "list",
            "--state",
            "open",
            "--json",
            "number,headRefName,body,labels",
        ),
        cwd=root,
        timeout=120,
    )
    if result.returncode != 0:
        return ()
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(item for item in payload if isinstance(item, dict) and _is_sdk_evolution_pr(item))


def ensure_label(
    root: Path, label: str, *, command_runner: CommandRunner | None = None
) -> CommandResult:
    """Create a label if it does not exist."""

    command_runner = command_runner or run_command
    return _call_runner(
        command_runner,
        ("gh", "label", "create", label, "--force"),
        cwd=root,
        timeout=120,
    )


def comment_pr(
    root: Path, number: int, body: str, *, command_runner: CommandRunner | None = None
) -> CommandResult:
    command_runner = command_runner or run_command
    return _call_runner(
        command_runner,
        ("gh", "pr", "comment", str(number), "--body", body),
        cwd=root,
        timeout=120,
    )


def close_pr(
    root: Path, number: int, body: str, *, command_runner: CommandRunner | None = None
) -> CommandResult:
    command_runner = command_runner or run_command
    return _call_runner(
        command_runner,
        ("gh", "pr", "close", str(number), "--comment", body),
        cwd=root,
        timeout=120,
    )


def add_label_to_pr(
    root: Path, number_or_url: str, label: str, *, command_runner: CommandRunner | None = None
) -> CommandResult:
    command_runner = command_runner or run_command
    return _call_runner(
        command_runner,
        ("gh", "pr", "edit", number_or_url, "--add-label", label),
        cwd=root,
        timeout=120,
    )


def _is_sdk_evolution_pr(item: dict[str, Any]) -> bool:
    head = str(item.get("headRefName") or "")
    if head.startswith("sdk-evolution"):
        return True
    labels = item.get("labels") or []
    return any(isinstance(label, dict) and label.get("name") == "sdk-evolution" for label in labels)
