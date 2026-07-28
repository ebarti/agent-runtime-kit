from __future__ import annotations

import shlex
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CODEX_RUNBOOK = Path(".codex/skills/agent-runtime-kit-upgrade/SKILL.md")
CLAUDE_RUNBOOK = Path(".claude/commands/agent-runtime-kit/upgrade.md")
PUBLIC_GUIDE = Path("docs/sdk-evolution-agent.md")
DESIGN_GUIDE = Path("docs/sdk-evolution-agent-design.md")
AUTHORITATIVE_DOCS = (CODEX_RUNBOOK, CLAUDE_RUNBOOK, PUBLIC_GUIDE, DESIGN_GUIDE)
RUNBOOKS = (CODEX_RUNBOOK, CLAUDE_RUNBOOK)
PUBLIC_DOCS = (PUBLIC_GUIDE, DESIGN_GUIDE)
RUNTIME_EXTRAS = {
    "claude-agent-sdk": "claude",
    "codex-agent-sdk": "codex",
    "antigravity-agent-sdk": "antigravity",
}
AGENT_MODULE = ("python", "-m", "examples.sdk_evolution_agent")
AUTH_MODULE = ("python", "-m", "examples.sdk_evolution_agent.auth")


def test_sdk_evolution_bash_commands_use_locked_uv() -> None:
    for path in AUTHORITATIVE_DOCS:
        for command in _bash_commands(path):
            _assert_locked_command(path, command)


def test_real_sdk_evolution_commands_match_runtime_extras() -> None:
    for path in AUTHORITATIVE_DOCS:
        for command in _agent_commands(path):
            _assert_real_runtime_command(path, command)


def test_public_guides_cover_every_runtime_extra_mapping() -> None:
    for path in PUBLIC_DOCS:
        commands = _agent_commands(path)
        observed = {
            runtime
            for command in commands
            if (runtime := _option_value(command, "--runtime")) in RUNTIME_EXTRAS
        }
        assert observed == set(RUNTIME_EXTRAS), f"incomplete runtime mapping in {path}"

    for path in RUNBOOKS:
        text = _read(path)
        for runtime, extra in RUNTIME_EXTRAS.items():
            assert f"`{runtime}` -> `--extra {extra}`" in text


def test_runbooks_include_report_and_implementation_commands() -> None:
    defaults = {
        CODEX_RUNBOOK: "codex-agent-sdk",
        CLAUDE_RUNBOOK: "claude-agent-sdk",
    }
    for path, runtime in defaults.items():
        commands = [
            command
            for command in _agent_commands(path)
            if _option_value(command, "--runtime") == runtime
        ]
        assert any("--implementation-enabled" not in command for command in commands)
        assert any("--implementation-enabled" in command for command in commands)


def test_fake_commands_are_pipeline_shape_checks_only() -> None:
    for path in PUBLIC_DOCS:
        commands = [
            command
            for command in _agent_commands(path)
            if _option_value(command, "--runtime") == "fake"
        ]
        assert commands, f"missing fake pipeline-shape command in {path}"
        for command in commands:
            assert "--inspect-candidates" not in command
            assert "--implementation-enabled" not in command
            assert "--draft-pr" not in command
        prose = _read(path).lower().replace("-", " ")
        assert "pipeline shape" in prose
        assert "never upgrade safety" in prose or "not upgrade safety" in prose


def test_candidate_inspection_remains_explicit_opt_in() -> None:
    for path in AUTHORITATIVE_DOCS:
        prose = " ".join(_read(path).lower().split())
        assert "--inspect-candidates" in prose
        assert _has_candidate_opt_in(prose)


def test_behavior_summary_is_in_artifacts_and_runbook_handoffs() -> None:
    for path in AUTHORITATIVE_DOCS:
        text = _read(path)
        assert "behavior_summary.json" in text
        for status in ("pass", "changed", "incomplete", "fail"):
            assert f"`{status}`" in text

    for path in RUNBOOKS:
        text = _read(path)
        normalized = " ".join(text.split())
        assert "`behavior_summary.json` status and reasons" in text
        assert "a missing or drifted locked baseline" in normalized
        assert "credential-scrubbed" in text
        for blocker in ("missing", "malformed", "unknown status", "`fail`", "`incomplete`"):
            assert blocker in text


def test_codex_auth_commands_use_the_locked_codex_extra() -> None:
    for path in AUTHORITATIVE_DOCS:
        for command in _bash_commands(path):
            payload = _command_payload(command)
            if _starts_with(payload, AUTH_MODULE) or _starts_with(payload, ("codex", "login")):
                assert _starts_with(
                    _command_argv(command),
                    ("uv", "run", "--locked", "--extra", "codex"),
                )


@pytest.mark.parametrize(
    "body",
    (
        "uv run --locked python -m examples.sdk_evolution_agent "
        "--runtime=codex-agent-sdk",
        "COMPLIANT='uv run --locked --extra codex python -m "
        "examples.sdk_evolution_agent' python -m examples.sdk_evolution_agent "
        "--runtime codex-agent-sdk --refresh-preview --inspect-candidates",
        "uv run --locked python -m examples.sdk_evolution_agent --help && "
        "python -m examples.sdk_evolution_agent --runtime codex-agent-sdk "
        "--refresh-preview --inspect-candidates",
    ),
)
def test_noncompliant_shell_forms_cannot_evade_locked_command_checks(
    tmp_path: Path, body: str
) -> None:
    path = tmp_path / "commands.md"
    path.write_text(f"```bash\n{body}\n```\n", encoding="utf-8")
    commands = _bash_commands(path)
    with pytest.raises(AssertionError):
        for command in commands:
            _assert_locked_command(path, command)
            _assert_real_runtime_command(path, command)


def test_runtime_equals_form_cannot_evade_runtime_extra_checks(tmp_path: Path) -> None:
    path = tmp_path / "commands.md"
    path.write_text(
        "```bash\n"
        "uv run --locked python -m examples.sdk_evolution_agent "
        "--runtime=codex-agent-sdk\n"
        "```\n",
        encoding="utf-8",
    )
    command = _agent_commands(path)[0]
    assert _option_value(command, "--runtime") == "codex-agent-sdk"
    with pytest.raises(AssertionError, match="runtime/extra mismatch"):
        _assert_real_runtime_command(path, command)


def test_echoes_and_comments_do_not_count_as_executable_commands(tmp_path: Path) -> None:
    path = tmp_path / "commands.md"
    path.write_text(
        "```bash\n"
        "echo uv run --locked --extra codex python -m "
        "examples.sdk_evolution_agent --runtime codex-agent-sdk "
        "--refresh-preview --inspect-candidates\n"
        "# uv run --locked --extra codex python -m "
        "examples.sdk_evolution_agent --runtime codex-agent-sdk "
        "--refresh-preview --inspect-candidates\n"
        "```\n",
        encoding="utf-8",
    )
    assert _agent_commands(path) == []


def test_unrelated_opt_in_prose_does_not_satisfy_candidate_consent() -> None:
    prose = "--inspect-candidates is automatic. draft pr creation is opt-in."
    assert not _has_candidate_opt_in(prose)


def _read(path: Path) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _agent_commands(path: Path) -> list[list[str]]:
    return [
        command
        for command in _bash_commands(path)
        if _starts_with(_command_payload(command), AGENT_MODULE)
    ]


def _bash_commands(path: Path) -> list[list[str]]:
    commands: list[list[str]] = []
    pending: list[str] = []
    in_bash = False
    for raw_line in _read(path).splitlines():
        stripped = raw_line.strip()
        if stripped == "```bash":
            in_bash = True
            pending = []
            continue
        if in_bash and stripped == "```":
            if pending:
                commands.extend(_split_shell_commands(" ".join(pending)))
            in_bash = False
            pending = []
            continue
        if not in_bash:
            continue
        if not stripped:
            if pending:
                commands.extend(_split_shell_commands(" ".join(pending)))
                pending = []
            continue
        continued = stripped.endswith("\\")
        pending.append(stripped[:-1].rstrip() if continued else stripped)
        if not continued:
            commands.extend(_split_shell_commands(" ".join(pending)))
            pending = []
    return commands


def _split_shell_commands(command: str) -> list[list[str]]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
    lexer.whitespace_split = True
    lexer.commenters = "#"
    commands: list[list[str]] = []
    pending: list[str] = []
    for token in lexer:
        if token and set(token) <= {";", "&", "|"}:
            if pending:
                commands.append(pending)
                pending = []
            continue
        pending.append(token)
    if pending:
        commands.append(pending)
    return commands


def _option_value(command: list[str], option: str) -> str | None:
    for index, part in enumerate(command):
        if part == option:
            return command[index + 1] if index + 1 < len(command) else None
        prefix = f"{option}="
        if part.startswith(prefix):
            return part.removeprefix(prefix) or None
    return None


def _assert_locked_command(path: Path, command: list[str]) -> None:
    argv = _command_argv(command)
    if _starts_with(argv, ("uv", "run")):
        assert _starts_with(argv, ("uv", "run", "--locked")), (
            f"unlocked uv command in {path}: {_display(command)}"
        )
    payload = _command_payload(command)
    if _starts_with(payload, AGENT_MODULE):
        assert _starts_with(argv, ("uv", "run", "--locked")), (
            f"bare actionable SDK evolution command in {path}: {_display(command)}"
        )


def _assert_real_runtime_command(path: Path, command: list[str]) -> None:
    runtime = _option_value(command, "--runtime")
    if runtime is None or runtime == "fake" or "--help" in command:
        return
    assert runtime in RUNTIME_EXTRAS, f"unknown runtime in {path}: {_display(command)}"
    extra = RUNTIME_EXTRAS[runtime]
    argv = _command_argv(command)
    assert _starts_with(argv, ("uv", "run", "--locked", "--extra", extra)), (
        f"runtime/extra mismatch in {path}: {_display(command)}"
    )
    assert _starts_with(_command_payload(command), AGENT_MODULE), (
        f"runtime command is not the SDK evolution entrypoint in {path}: {_display(command)}"
    )
    assert "--refresh-preview" in command, (
        f"missing refresh in {path}: {_display(command)}"
    )
    assert "--inspect-candidates" in command, (
        f"missing candidate inspection in {path}: {_display(command)}"
    )


def _command_argv(command: list[str]) -> list[str]:
    index = 0
    while index < len(command) and _is_assignment(command[index]):
        index += 1
    if index >= len(command) or command[index] != "env":
        return command[index:]

    index += 1
    while index < len(command):
        token = command[index]
        if _is_assignment(token):
            index += 1
        elif token in {"-u", "--unset"}:
            index += 2
        elif token.startswith("--unset="):
            index += 1
        elif token == "--":
            index += 1
            break
        else:
            break
    return command[index:]


def _command_payload(command: list[str]) -> list[str]:
    argv = _command_argv(command)
    if not _starts_with(argv, ("uv", "run")):
        return argv

    index = 2
    while index < len(argv):
        token = argv[index]
        if token == "--":
            return argv[index + 1 :]
        if token == "--extra":
            index += 2
            continue
        if token == "--locked" or token.startswith("--extra="):
            index += 1
            continue
        break
    return argv[index:]


def _is_assignment(token: str) -> bool:
    name, separator, _value = token.partition("=")
    return bool(separator and name and name.replace("_", "a").isalnum() and not name[0].isdigit())


def _has_candidate_opt_in(prose: str) -> bool:
    normalized = prose.replace("opt in", "opt-in")
    sentences = normalized.split(".")
    return (
        "`--inspect-candidates` is explicit consent" in normalized
        or "candidate inspection is opt-in" in normalized
        or any(
            "candidate inspection" in sentence and "so it is opt-in" in sentence
            for sentence in sentences
        )
    )


def _starts_with(command: list[str], sequence: tuple[str, ...]) -> bool:
    return tuple(command[: len(sequence)]) == sequence


def _display(command: list[str]) -> str:
    return shlex.join(command)
