from __future__ import annotations

from pathlib import Path

from examples.sdk_evolution_agent.models import CommandResult
from examples.sdk_evolution_agent.pr import build_pr_marker, parse_pr_marker
from examples.sdk_evolution_agent.status_issue import render_status_body, update_status_issue


def test_pr_marker_round_trip() -> None:
    marker = build_pr_marker({"claude-agent-sdk": "0.2.110"}, run_id="run-1")

    assert parse_pr_marker(marker) == {
        "packages": {"claude-agent-sdk": "0.2.110"},
        "run_id": "run-1",
    }
    assert parse_pr_marker("no marker") is None


def test_status_body_renders_candidates_and_blockers(tmp_path: Path) -> None:
    body = render_status_body(
        tmp_path / "reports" / "run-1",
        evidence={
            "update_candidates": [
                {
                    "package": "claude-agent-sdk",
                    "from_version": "0.2.1",
                    "to_version": "0.2.2",
                }
            ],
            "update_candidates_beyond_cap": [],
        },
        architecture={"manual_design_required": True, "safe_to_implement": False},
    )

    assert "claude-agent-sdk" in body
    assert "manual_design_required" in body
    assert "safe_to_implement=false" in body


def test_status_issue_updates_existing_issue(tmp_path: Path) -> None:
    run_dir = tmp_path / "reports" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "evidence.json").write_text(
        '{"update_candidates":[{"package":"claude-agent-sdk","from_version":"1","to_version":"2"}]}',
        encoding="utf-8",
    )
    (run_dir / "architecture_decision.json").write_text(
        '{"manual_design_required":false,"safe_to_implement":true}',
        encoding="utf-8",
    )
    commands: list[tuple[str, ...]] = []

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        del cwd, timeout
        commands.append(command)
        if command[:3] == ("gh", "issue", "list"):
            return CommandResult(
                command=command,
                returncode=0,
                stdout='[{"number":7,"state":"OPEN","title":"SDK evolution status"}]',
            )
        return CommandResult(command=command, returncode=0)

    update_status_issue(run_dir, command_runner=runner)

    assert ("gh", "issue", "edit", "7", "--body") == commands[3][:5]
