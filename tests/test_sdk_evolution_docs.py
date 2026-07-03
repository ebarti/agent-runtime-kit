from __future__ import annotations

import shlex
from pathlib import Path

from examples.sdk_evolution_agent.cli import parse_args
from examples.sdk_evolution_agent.models import CommandResult
from examples.sdk_evolution_agent.preflight import validate_run_plan

DOCS_WITH_COMMANDS = (
    Path("docs/sdk-evolution-agent.md"),
    Path(".claude/commands/agent-runtime-kit/upgrade.md"),
    Path(".codex/skills/agent-runtime-kit-upgrade/SKILL.md"),
)


def test_documented_sdk_evolution_commands_parse_and_preflight(tmp_path: Path) -> None:
    for command in _documented_agent_commands():
        argv = _agent_argv(command)
        options = parse_args(argv)
        options = _with_workspace(options, tmp_path)
        assert validate_run_plan(options, tmp_path, command_runner=_runner) == ()


def _documented_agent_commands() -> list[str]:
    commands: list[str] = []
    for path in DOCS_WITH_COMMANDS:
        in_bash = False
        pending: list[str] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip() == "```bash":
                in_bash = True
                pending = []
                continue
            if line.strip() == "```":
                in_bash = False
                pending = []
                continue
            if not in_bash:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith("env "):
                continue
            if "python -m examples.sdk_evolution_agent" in stripped and ".auth" not in stripped:
                if "--help" in stripped:
                    continue
                pending = [stripped.rstrip("\\").strip()]
                if not stripped.endswith("\\"):
                    commands.append(" ".join(pending))
                    pending = []
                continue
            if pending:
                pending.append(stripped.rstrip("\\").strip())
                if not stripped.endswith("\\"):
                    commands.append(" ".join(pending))
                    pending = []
    return commands


def _agent_argv(command: str) -> list[str]:
    parts = shlex.split(command)
    index = parts.index("examples.sdk_evolution_agent")
    return parts[index + 1 :]


def _with_workspace(options: object, workspace: Path):
    return type(options)(**{**options.__dict__, "workspace": workspace})


def _runner(command: tuple[str, ...], *, cwd: Path | None = None) -> CommandResult:
    del cwd
    if command == ("git", "branch", "--show-current"):
        return CommandResult(command=command, returncode=0, stdout="sdk-evolution/test\n")
    if command == ("git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"):
        return CommandResult(command=command, returncode=0, stdout="origin/main\n")
    if command == ("git", "status", "--porcelain"):
        return CommandResult(command=command, returncode=0, stdout="")
    return CommandResult(command=command, returncode=0)
