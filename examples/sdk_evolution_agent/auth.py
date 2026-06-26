"""Authentication preflight helpers for the SDK evolution workflow."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from examples.sdk_evolution_agent.collectors import cutoff_free_env
from examples.sdk_evolution_agent.models import CommandResult

DEFAULT_CODEX_HOME = Path("~/.codex_agent_runtime_sdk").expanduser()
USER_CODEX_HOME_DIRNAME = ".codex"
CodexCommandRunner = Callable[..., CommandResult]


@dataclass(frozen=True)
class CodexAuthResult:
    """Result of preparing the dedicated Codex auth home."""

    ok: bool
    codex_home: Path
    initial_status: CommandResult
    final_status: CommandResult
    auth_copied: bool = False
    removed_env: tuple[str, ...] = ()
    message: str = ""


def ensure_codex_sdk_auth(
    *,
    codex_home: Path = DEFAULT_CODEX_HOME,
    home_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    command_runner: CodexCommandRunner | None = None,
) -> CodexAuthResult:
    """Ensure the dedicated SDK evolution Codex home has fresh supported auth."""

    command_runner = command_runner or run_codex_command
    home = Path.home() if home_dir is None else Path(home_dir).expanduser()
    codex_home = codex_home.expanduser()
    source_auth = home / USER_CODEX_HOME_DIRNAME / "auth.json"
    target_auth = codex_home / "auth.json"
    auth_copied = _copy_newer_file(source_auth, target_auth, mode=0o600)

    clean_env, removed = cutoff_free_env(env)
    clean_env["CODEX_HOME"] = str(codex_home)

    status = command_runner(("codex", "login", "status"), env=clean_env, timeout=30)
    if status.returncode == 0:
        verb = "refreshed" if auth_copied else "ready"
        return CodexAuthResult(
            ok=True,
            codex_home=codex_home,
            initial_status=status,
            final_status=status,
            auth_copied=auth_copied,
            removed_env=removed,
            message=f"Codex auth {verb} for CODEX_HOME={codex_home}",
        )

    return CodexAuthResult(
        ok=False,
        codex_home=codex_home,
        initial_status=status,
        final_status=status,
        auth_copied=auth_copied,
        removed_env=removed,
        message=codex_auth_recovery_message(codex_home, source_auth=source_auth),
    )


def run_codex_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    timeout: int = 60,
) -> CommandResult:
    """Run a Codex CLI auth command."""

    try:
        completed = subprocess.run(
            tuple(command),
            env=dict(env),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        return CommandResult(tuple(command), returncode=127, stderr=str(exc))
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def prepare_isolated_codex_home(
    *,
    codex_home: Path = DEFAULT_CODEX_HOME,
    home_dir: Path | None = None,
) -> Path:
    """Return the isolated SDK evolution Codex home, refreshed from user auth."""

    home = Path.home() if home_dir is None else Path(home_dir).expanduser()
    codex_home = codex_home.expanduser()
    _copy_newer_file(
        home / USER_CODEX_HOME_DIRNAME / "auth.json",
        codex_home / "auth.json",
        mode=0o600,
    )
    return codex_home


def codex_auth_recovery_message(
    codex_home: Path = DEFAULT_CODEX_HOME,
    *,
    source_auth: Path | None = None,
) -> str:
    """Return supported recovery commands for an unauthenticated Codex home."""

    codex_home = codex_home.expanduser()
    source_auth = source_auth or Path.home() / USER_CODEX_HOME_DIRNAME / "auth.json"
    return f"""Codex auth is not ready for CODEX_HOME={codex_home}.
The SDK evolution workflow mirrors Mestre: it refreshes isolated auth from
{source_auth}, then runs Codex with the isolated CODEX_HOME.

Run the supported Codex login flow for your normal Codex home, then rerun this
helper before starting the SDK evolution agent:

  uv run --extra codex codex login --device-auth
  uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
"""


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point for auth preflights."""

    parser = argparse.ArgumentParser(description="SDK evolution auth preflight helpers.")
    subparsers = parser.add_subparsers(dest="command")
    ensure = subparsers.add_parser(
        "ensure-codex",
        help="Prepare and verify the dedicated SDK evolution Codex auth home.",
    )
    ensure.add_argument(
        "--codex-home",
        type=Path,
        default=None,
        help="Dedicated Codex home to prepare.",
    )
    args = parser.parse_args(argv)
    if args.command != "ensure-codex":
        parser.print_help()
        return 2

    codex_home = args.codex_home or Path(os.environ.get("CODEX_HOME", DEFAULT_CODEX_HOME))
    result = ensure_codex_sdk_auth(codex_home=codex_home)
    print(result.message)
    return 0 if result.ok else 1


def _copy_newer_file(source: Path, target: Path, *, mode: int | None = None) -> bool:
    """Copy source to target when source is newer or target is missing."""

    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    if not source.exists():
        if target.exists() and mode is not None:
            target.chmod(mode)
        return False
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        if mode is not None:
            target.chmod(mode)
        return False
    shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)
    return True


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
