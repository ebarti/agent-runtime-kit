#!/usr/bin/env python3
"""Run the SDK evolution upgrade workflow from a fresh local worktree."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
for path in (REPO_ROOT, SRC_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from examples.sdk_evolution_agent.models import DEFAULT_PACKAGES  # noqa: E402

DEFAULT_RUNTIME = "codex-agent-sdk"
DEFAULT_BASE = "origin/main"
DEFAULT_WORKTREE_PARENT = Path("/tmp")
DEFAULT_BRANCH_PREFIX = "sdk-evolution-upgrade"
DEFAULT_PR_BASE = "main"
DEFAULT_COMMIT_MESSAGE = "Run SDK evolution update"
DEFAULT_PR_TITLE = "Run SDK evolution update across vendor packages"
CommandRunner = Callable[..., int]


@dataclass(frozen=True)
class UpgradeConfig:
    """Configuration for one SDK evolution upgrade run."""

    repo_root: Path
    runtime: str = DEFAULT_RUNTIME
    base: str = DEFAULT_BASE
    worktree_parent: Path = DEFAULT_WORKTREE_PARENT
    worktree_path: Path | None = None
    branch_name: str | None = None
    branch_prefix: str = DEFAULT_BRANCH_PREFIX
    report_only: bool = False
    draft_pr: bool = True
    pr_base: str = DEFAULT_PR_BASE
    commit_message: str = DEFAULT_COMMIT_MESSAGE
    pr_title: str = DEFAULT_PR_TITLE


@dataclass(frozen=True)
class UpgradeRun:
    """Resolved paths and names for one upgrade run."""

    branch_name: str
    worktree_path: Path


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and run the workflow."""

    config = parse_args(argv)
    run = run_upgrade(config)
    print(f"SDK evolution worktree: {run.worktree_path}")
    print(f"SDK evolution branch: {run.branch_name}")
    return 0


def parse_args(argv: Sequence[str] | None = None) -> UpgradeConfig:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Create a fresh worktree and run the agent-runtime-kit SDK evolution "
            "upgrade workflow for all tracked vendor SDK packages."
        )
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository root to create the worktree from.",
    )
    parser.add_argument(
        "--runtime",
        default=DEFAULT_RUNTIME,
        help="agent-runtime-kit runtime used for AI-backed analysis and review.",
    )
    parser.add_argument("--base", default=DEFAULT_BASE, help="Git base for the fresh worktree.")
    parser.add_argument(
        "--worktree-parent",
        type=Path,
        default=DEFAULT_WORKTREE_PARENT,
        help="Directory that will receive the unique worktree.",
    )
    parser.add_argument(
        "--worktree-path",
        type=Path,
        help="Explicit worktree path. Must not already exist.",
    )
    parser.add_argument(
        "--branch-name",
        help="Explicit branch name. Defaults to a unique timestamped branch.",
    )
    parser.add_argument(
        "--branch-prefix",
        default=DEFAULT_BRANCH_PREFIX,
        help="Prefix for generated branch and worktree names.",
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Stop after the report-only evidence and decision pass.",
    )
    parser.add_argument(
        "--no-draft-pr",
        action="store_true",
        help="Run implementation without creating a draft PR.",
    )
    parser.add_argument("--pr-base", default=DEFAULT_PR_BASE, help="Base branch for draft PRs.")
    parser.add_argument(
        "--commit-message",
        default=DEFAULT_COMMIT_MESSAGE,
        help="Commit message for the autonomous SDK update.",
    )
    parser.add_argument("--pr-title", default=DEFAULT_PR_TITLE, help="Draft PR title.")
    args = parser.parse_args(argv)
    return UpgradeConfig(
        repo_root=args.repo_root.expanduser().resolve(),
        runtime=args.runtime,
        base=args.base,
        worktree_parent=args.worktree_parent.expanduser(),
        worktree_path=args.worktree_path.expanduser() if args.worktree_path else None,
        branch_name=args.branch_name,
        branch_prefix=args.branch_prefix,
        report_only=args.report_only,
        draft_pr=not args.no_draft_pr,
        pr_base=args.pr_base,
        commit_message=args.commit_message,
        pr_title=args.pr_title,
    )


def run_upgrade(
    config: UpgradeConfig,
    *,
    command_runner: CommandRunner | None = None,
) -> UpgradeRun:
    """Create a fresh worktree and run report-only plus optional implementation."""

    runner = command_runner or run_command
    branch_name = config.branch_name or generate_unique_name(config.branch_prefix)
    worktree_path = resolve_worktree_path(config, branch_name=branch_name)
    env = cutoff_free_env()

    run_checked(runner, ("git", "fetch", "origin", "--prune"), cwd=config.repo_root, env=env)
    run_checked(
        runner,
        ("git", "worktree", "add", "-b", branch_name, str(worktree_path), config.base),
        cwd=config.repo_root,
        env=env,
    )
    if config.draft_pr and not config.report_only:
        run_checked(runner, ("gh", "auth", "status"), cwd=worktree_path, env=env)

    run_checked(
        runner,
        ("uv", "run", "python", "-m", "examples.sdk_evolution_agent", "--help"),
        cwd=worktree_path,
        env=env,
    )
    if config.runtime == "codex-agent-sdk":
        run_checked(
            runner,
            (
                "uv",
                "run",
                "--extra",
                "codex",
                "python",
                "-m",
                "examples.sdk_evolution_agent.auth",
                "ensure-codex",
            ),
            cwd=worktree_path,
            env=env,
        )

    run_checked(
        runner,
        build_agent_command(config, implementation_enabled=False, branch_name=branch_name),
        cwd=worktree_path,
        env=env,
    )
    if not config.report_only:
        run_checked(
            runner,
            build_agent_command(config, implementation_enabled=True, branch_name=branch_name),
            cwd=worktree_path,
            env=env,
        )
    return UpgradeRun(branch_name=branch_name, worktree_path=worktree_path)


def generate_unique_name(prefix: str) -> str:
    """Return a branch-safe unique name."""

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{timestamp}-{uuid.uuid4().hex[:8]}"


def resolve_worktree_path(config: UpgradeConfig, *, branch_name: str) -> Path:
    """Resolve the worktree path and fail before colliding with existing state."""

    worktree_path = config.worktree_path or config.worktree_parent / branch_name
    worktree_path = worktree_path.expanduser()
    if worktree_path.exists():
        raise SystemExit(f"worktree path already exists: {worktree_path}")
    return worktree_path


def build_agent_command(
    config: UpgradeConfig,
    *,
    implementation_enabled: bool,
    branch_name: str,
) -> tuple[str, ...]:
    """Build the SDK evolution agent command for all tracked packages."""

    command = ["uv", "run"]
    if config.runtime == "codex-agent-sdk":
        command.extend(("--extra", "codex"))
    command.extend(
        (
            "python",
            "-m",
            "examples.sdk_evolution_agent",
            "--runtime",
            config.runtime,
            "--refresh-preview",
        )
    )
    if implementation_enabled:
        command.extend(
            [
                "--implementation-enabled",
                "--branch-name",
                branch_name,
                "--pr-base",
                config.pr_base,
                "--commit-message",
                config.commit_message,
                "--pr-title",
                config.pr_title,
            ]
        )
        if config.draft_pr:
            command.append("--draft-pr")
    for package in DEFAULT_PACKAGES:
        command.extend(("--package", package))
    return tuple(command)


def cutoff_free_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment with uv freshness cutoff variables removed."""

    clean = dict(env or os.environ)
    clean.pop("VIRTUAL_ENV", None)
    for key in tuple(clean):
        if (
            key == "UV_EXCLUDE_NEWER"
            or key.startswith("UV_EXCLUDE_NEWER_")
            or key.endswith("_EXCLUDE_NEWER")
        ):
            clean.pop(key, None)
    return clean


def run_checked(
    command_runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> None:
    """Run one command and stop on failure."""

    result = command_runner(tuple(command), cwd=cwd, env=env)
    if result != 0:
        rendered = " ".join(command)
        raise SystemExit(f"command failed ({result}) in {cwd}: {rendered}")


def run_command(command: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> int:
    """Run a command, streaming output directly to the terminal."""

    print(f"$ {' '.join(command)}", flush=True)
    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        env=dict(env),
        check=False,
    )
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
