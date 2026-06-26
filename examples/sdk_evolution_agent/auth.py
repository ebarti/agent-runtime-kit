"""Authentication preflight helpers for the SDK evolution workflow."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from examples.sdk_evolution_agent.collectors import cutoff_free_env
from examples.sdk_evolution_agent.models import CommandResult

DEFAULT_CODEX_HOME = Path("~/.codex_agent_runtime_sdk").expanduser()
CodexCommandRunner = Callable[..., CommandResult]


@dataclass(frozen=True)
class CodexAuthResult:
    """Result of preparing the dedicated Codex auth home."""

    ok: bool
    codex_home: Path
    initial_status: CommandResult
    final_status: CommandResult
    refresh_result: CommandResult | None = None
    removed_env: tuple[str, ...] = ()
    message: str = ""


def ensure_codex_sdk_auth(
    *,
    codex_home: Path = DEFAULT_CODEX_HOME,
    env: Mapping[str, str] | None = None,
    command_runner: CodexCommandRunner | None = None,
) -> CodexAuthResult:
    """Ensure the dedicated SDK evolution Codex home has fresh supported auth."""

    command_runner = command_runner or run_codex_command
    codex_home = codex_home.expanduser()
    codex_home.mkdir(mode=0o700, parents=True, exist_ok=True)
    codex_home.chmod(0o700)

    clean_env, removed = cutoff_free_env(env)
    clean_env["CODEX_HOME"] = str(codex_home)

    initial_status = command_runner(("codex", "login", "status"), env=clean_env, timeout=30)
    if initial_status.returncode == 0:
        return CodexAuthResult(
            ok=True,
            codex_home=codex_home,
            initial_status=initial_status,
            final_status=initial_status,
            removed_env=removed,
            message=f"Codex auth ready for CODEX_HOME={codex_home}",
        )

    refresh_result: CommandResult | None = None
    token = clean_env.get("CODEX_ACCESS_TOKEN")
    api_key = clean_env.get("OPENAI_API_KEY")
    if token:
        refresh_result = command_runner(
            ("codex", "login", "--with-access-token"),
            env=clean_env,
            stdin=_stdin_secret(token),
            timeout=60,
        )
    elif api_key:
        refresh_result = command_runner(
            ("codex", "login", "--with-api-key"),
            env=clean_env,
            stdin=_stdin_secret(api_key),
            timeout=60,
        )

    if refresh_result is not None and refresh_result.returncode == 0:
        final_status = command_runner(("codex", "login", "status"), env=clean_env, timeout=30)
        if final_status.returncode == 0:
            return CodexAuthResult(
                ok=True,
                codex_home=codex_home,
                initial_status=initial_status,
                final_status=final_status,
                refresh_result=refresh_result,
                removed_env=removed,
                message=f"Codex auth refreshed for CODEX_HOME={codex_home}",
            )
    else:
        final_status = initial_status

    return CodexAuthResult(
        ok=False,
        codex_home=codex_home,
        initial_status=initial_status,
        final_status=final_status,
        refresh_result=refresh_result,
        removed_env=removed,
        message=codex_auth_recovery_message(codex_home),
    )


def run_codex_command(
    command: Sequence[str],
    *,
    env: Mapping[str, str],
    stdin: str | None = None,
    timeout: int = 60,
) -> CommandResult:
    """Run a Codex CLI auth command without exposing stdin secrets."""

    try:
        completed = subprocess.run(
            tuple(command),
            env=dict(env),
            input=stdin,
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


def codex_auth_recovery_message(codex_home: Path = DEFAULT_CODEX_HOME) -> str:
    """Return supported recovery commands for an unauthenticated Codex home."""

    codex_home = codex_home.expanduser()
    return f"""Codex auth is not ready for CODEX_HOME={codex_home}.
Run one supported auth path before starting the SDK evolution agent:

  env CODEX_HOME="{codex_home}" uv run --extra codex codex login --device-auth

Or provide a supported non-interactive credential and rerun the auth helper:

  env CODEX_HOME="{codex_home}" CODEX_ACCESS_TOKEN="$CODEX_ACCESS_TOKEN" \\
    uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex

  env CODEX_HOME="{codex_home}" OPENAI_API_KEY="$OPENAI_API_KEY" \\
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


def _stdin_secret(value: str) -> str:
    return value if value.endswith("\n") else f"{value}\n"


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
