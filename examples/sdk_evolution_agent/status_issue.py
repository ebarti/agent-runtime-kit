"""Create or update the rolling SDK evolution status issue."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.collectors import CommandRunner, _call_runner, run_command
from examples.sdk_evolution_agent.models import CommandResult

STATUS_TITLE = "SDK evolution status"
STATUS_LABEL = "sdk-evolution"
CAPABILITY_LABEL = "sdk-evolution-capability"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    update_status_issue(args.run_dir)
    return 0


def update_status_issue(
    run_dir: Path, *, command_runner: CommandRunner | None = None
) -> tuple[CommandResult, ...]:
    """Update the rolling status issue from one run directory."""

    runner = command_runner or run_command
    evidence = _load_json(run_dir / "evidence.json", {})
    architecture = _load_json(run_dir / "architecture_decision.json", {})
    body = render_status_body(run_dir, evidence=evidence, architecture=architecture)
    findings = _status_findings(evidence, architecture)
    results: list[CommandResult] = [
        _gh(runner, ("gh", "label", "create", STATUS_LABEL, "--force"), cwd=run_dir),
        _gh(runner, ("gh", "label", "create", CAPABILITY_LABEL, "--force"), cwd=run_dir),
    ]
    existing = _find_issue(STATUS_TITLE, runner=runner, cwd=run_dir)
    if findings:
        if existing:
            results.append(
                _gh(
                    runner,
                    ("gh", "issue", "edit", str(existing["number"]), "--body", body),
                    cwd=run_dir,
                )
            )
            if existing.get("state") == "CLOSED":
                results.append(
                    _gh(runner, ("gh", "issue", "reopen", str(existing["number"])), cwd=run_dir)
                )
        else:
            results.append(
                _gh(
                    runner,
                    (
                        "gh",
                        "issue",
                        "create",
                        "--title",
                        STATUS_TITLE,
                        "--label",
                        STATUS_LABEL,
                        "--body",
                        body,
                    ),
                    cwd=run_dir,
                )
            )
    elif existing and existing.get("state") != "CLOSED":
        results.append(
            _gh(
                runner,
                (
                    "gh",
                    "issue",
                    "close",
                    str(existing["number"]),
                    "--comment",
                    f"All current as of {_timestamp()}.",
                ),
                cwd=run_dir,
            )
        )
    results.extend(_file_capability_issues(architecture, runner=runner, cwd=run_dir))
    return tuple(results)


def render_status_body(
    run_dir: Path, *, evidence: dict[str, Any], architecture: dict[str, Any]
) -> str:
    candidates = evidence.get("update_candidates") or []
    beyond = evidence.get("update_candidates_beyond_cap") or []
    blockers = _blockers(architecture)
    artifact = _workflow_artifact_url()
    lines = [
        f"Updated: {_timestamp()}",
        "",
        "| Type | Package | From | To | Note |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in candidates:
        if isinstance(item, dict):
            lines.append(_candidate_row("adoptable", item, ""))
    for item in beyond:
        if isinstance(item, dict):
            note = str(item.get("blocked_by_cap") or item.get("cutoff_delayed_until") or "")
            lines.append(_candidate_row("beyond-cap", item, note))
    if not candidates and not beyond:
        lines.append("| none | - | - | - | - |")
    lines.extend(["", "## Gate Blockers", ""])
    lines.extend([f"- {blocker}" for blocker in blockers] or ["- None"])
    lines.extend(
        [
            "",
            "## Artifacts",
            "",
            f"- Run directory: `{run_dir}`",
            f"- Workflow artifact: {artifact or 'not available'}",
        ]
    )
    return "\n".join(lines) + "\n"


def _candidate_row(kind: str, item: dict[str, Any], note: str) -> str:
    return "| {kind} | {package} | {from_version} | {to_version} | {note} |".format(
        kind=kind,
        package=item.get("package", ""),
        from_version=item.get("from_version", ""),
        to_version=item.get("to_version", ""),
        note=note,
    )


def _status_findings(evidence: dict[str, Any], architecture: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    findings.extend(str(item) for item in evidence.get("update_candidates") or [])
    findings.extend(str(item) for item in evidence.get("update_candidates_beyond_cap") or [])
    findings.extend(_blockers(architecture))
    return findings


def _blockers(architecture: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    if architecture.get("manual_design_required"):
        blockers.append("manual_design_required")
    if architecture.get("safe_to_implement") is False:
        blockers.append("safe_to_implement=false")
    blockers.extend(str(item) for item in architecture.get("uncertainty") or [])
    return blockers


def _file_capability_issues(
    architecture: dict[str, Any], *, runner: CommandRunner, cwd: Path
) -> tuple[CommandResult, ...]:
    results: list[CommandResult] = []
    for finding in architecture.get("findings") or []:
        if (
            not isinstance(finding, dict)
            or finding.get("classification") != "capability-opportunity"
        ):
            continue
        summary = str(finding.get("summary") or "SDK evolution capability opportunity")[:80]
        title = f"[sdk-evolution] {summary}"
        if _find_issue(title, runner=runner, cwd=cwd):
            continue
        body = json.dumps(finding, indent=2, sort_keys=True)
        results.append(
            _gh(
                runner,
                (
                    "gh",
                    "issue",
                    "create",
                    "--title",
                    title,
                    "--label",
                    CAPABILITY_LABEL,
                    "--body",
                    body,
                ),
                cwd=cwd,
            )
        )
    return tuple(results)


def _find_issue(title: str, *, runner: CommandRunner, cwd: Path) -> dict[str, Any] | None:
    result = _gh(
        runner,
        (
            "gh",
            "issue",
            "list",
            "--state",
            "all",
            "--search",
            f"{title} in:title",
            "--json",
            "number,state,title",
        ),
        cwd=cwd,
    )
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    for item in payload:
        if isinstance(item, dict) and item.get("title") == title:
            return item
    return None


def _gh(runner: CommandRunner, command: tuple[str, ...], *, cwd: Path) -> CommandResult:
    return _call_runner(runner, command, cwd=cwd, timeout=120)


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _workflow_artifact_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL")
    repo = os.environ.get("GITHUB_REPOSITORY")
    run_id = os.environ.get("GITHUB_RUN_ID")
    if not (server and repo and run_id):
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
