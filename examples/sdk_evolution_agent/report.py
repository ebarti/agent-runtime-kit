"""Report generation for SDK evolution runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.behavior import (
    assess_behavior_payload,
    behavior_expectations_from_evidence,
)
from examples.sdk_evolution_agent.models import RunContext, to_jsonable


def write_json(path: Path, payload: Any) -> None:
    """Write JSON with stable formatting."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(to_jsonable(payload), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def write_run_report(
    context: RunContext,
    *,
    config: dict[str, Any],
    evidence: dict[str, Any],
    snapshots: list[dict[str, Any]],
    api_diffs: list[dict[str, Any]],
    release_notes: list[dict[str, Any]],
    behavior: dict[str, Any],
    current_state: dict[str, Any],
    direction: dict[str, Any],
    architecture: dict[str, Any],
    implementation: dict[str, Any],
    review: dict[str, Any],
    pr_body: str | None = None,
) -> Path:
    """Write all run artifacts and return report.md."""

    context.report_root.mkdir(parents=True, exist_ok=True)
    write_json(context.report_root / "config.json", config)
    write_json(context.report_root / "evidence.json", evidence)
    write_json(context.report_root / "release_notes.json", release_notes)
    write_json(context.report_root / "api_diffs.json", api_diffs)
    write_json(context.report_root / "behavior_probes.json", behavior.get("results", []))
    write_json(context.report_root / "behavior_diffs.json", behavior.get("diffs", []))
    write_json(
        context.report_root / "behavior_summary.json",
        assess_behavior_payload(
            behavior,
            expectations=behavior_expectations_from_evidence(evidence),
        ),
    )
    write_json(context.report_root / "direction_analysis.json", direction)
    write_json(context.report_root / "architecture_decision.json", architecture)
    write_json(context.report_root / "implementation_summary.json", implementation)
    write_json(context.report_root / "review.json", review)
    write_json(context.report_root / "current_state.json", current_state)
    snapshots_dir = context.report_root / "api_snapshots"
    snapshots_dir.mkdir(exist_ok=True)
    for index, snapshot in enumerate(snapshots, start=1):
        package = str(snapshot.get("package", "snapshot")).replace("/", "-")
        write_json(snapshots_dir / f"{index:02d}-{package}.json", snapshot)
    if pr_body is not None:
        (context.report_root / "draft_pr_body.md").write_text(pr_body, encoding="utf-8")
    report_path = context.report_root / "report.md"
    report_path.write_text(
        render_markdown_report(
            config=config,
            evidence=evidence,
            snapshots=snapshots,
            api_diffs=api_diffs,
            release_notes=release_notes,
            behavior=behavior,
            current_state=current_state,
            direction=direction,
            architecture=architecture,
            implementation=implementation,
            review=review,
        ),
        encoding="utf-8",
    )
    return report_path


def render_markdown_report(
    *,
    config: dict[str, Any],
    evidence: dict[str, Any],
    snapshots: list[dict[str, Any]],
    api_diffs: list[dict[str, Any]],
    release_notes: list[dict[str, Any]],
    behavior: dict[str, Any],
    current_state: dict[str, Any],
    direction: dict[str, Any],
    architecture: dict[str, Any],
    implementation: dict[str, Any],
    review: dict[str, Any],
) -> str:
    """Render the human-readable local report."""

    packages = evidence.get("packages", [])
    package_lines = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        package_lines.append(
            "- {name}: locked={locked} installed={installed} latest={latest}".format(
                name=package.get("name"),
                locked=package.get("locked_version"),
                installed=package.get("installed_version"),
                latest=package.get("latest_version"),
            )
        )
    manual = architecture.get("manual_design_required")
    recursive = architecture.get("recursive_self_adaptation_impact")
    release_lines = [
        "- {package}: {status} ({from_version} -> {to_version})".format(
            package=item.get("package"),
            status=item.get("status"),
            from_version=item.get("from_version"),
            to_version=item.get("to_version"),
        )
        for item in release_notes
        if isinstance(item, dict) and item.get("to_version")
    ]
    behavior_summary = assess_behavior_payload(
        behavior,
        expectations=behavior_expectations_from_evidence(evidence),
    )
    behavior_diffs = behavior.get("diffs", [])
    behavior_reasons = behavior_summary.get("reasons", [])
    snapshot_errors = [
        snapshot
        for snapshot in snapshots
        if isinstance(snapshot, dict) and snapshot.get("import_error")
    ]
    snapshot_error_lines = [
        "- {package}@{version} ({source}): {error}".format(
            package=snapshot.get("package"),
            version=snapshot.get("version"),
            source=snapshot.get("source"),
            error=_one_line(snapshot.get("import_error")),
        )
        for snapshot in snapshot_errors
    ]
    behavior_reason_lines = [
        f"- {_one_line(reason)}"
        for reason in behavior_reasons
        if isinstance(reason, str) and reason
    ]
    promotion = current_state.get("promotion", {}) if isinstance(current_state, dict) else {}
    return "\n".join(
        [
            "# SDK Evolution Agent Report",
            "",
            "## Run",
            "",
            f"- Runtime: `{config.get('runtime')}`",
            f"- Implementation enabled: `{config.get('implementation_enabled')}`",
            f"- Draft PR enabled: `{config.get('draft_pr')}`",
            "",
            "## Upstream Evidence",
            "",
            *(package_lines or ["- No package evidence collected."]),
            "",
            "## API Snapshots",
            "",
            f"- Status: `{'incomplete' if snapshot_errors else 'pass'}`",
            f"- Snapshot count: `{len(snapshots)}`",
            f"- Import or execution errors: `{len(snapshot_errors)}`",
            *(snapshot_error_lines or ["- No snapshot errors recorded."]),
            "",
            "## API Diffs",
            "",
            f"- Diff count: `{len(api_diffs)}`",
            "",
            "## Release Notes",
            "",
            *(release_lines or ["- No SDK update release-note evidence required."]),
            "",
            "## Behavior Probes",
            "",
            f"- Status: `{behavior_summary.get('status')}`",
            f"- Changed contracts: `{behavior_summary.get('changed_count')}`",
            f"- Breaking contracts: `{behavior_summary.get('breaking_count')}`",
            f"- Contract failures: `{behavior_summary.get('contract_failure_count')}`",
            f"- Probe errors: `{behavior_summary.get('probe_error_count')}`",
            f"- Skipped probes: `{behavior_summary.get('skipped_count')}`",
            f"- Missing comparisons: `{behavior_summary.get('missing_comparison_count')}`",
            f"- Malformed evidence: `{behavior_summary.get('malformed_count')}`",
            f"- Diff count: `{len(behavior_diffs)}`",
            *(behavior_reason_lines or ["- No behavior evidence issues recorded."]),
            "",
            "## Direction Of Travel",
            "",
            "```json",
            json.dumps(direction, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Architecture Decision",
            "",
            f"- Manual design required: `{manual}`",
            f"- Recursive self-adaptation impact: `{recursive}`",
            f"- Safe to implement: `{architecture.get('safe_to_implement')}`",
            "",
            "```json",
            json.dumps(architecture, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Implementation Summary",
            "",
            "```json",
            json.dumps(implementation, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Current State Baseline",
            "",
            f"- Promotion status: `{promotion.get('status')}`",
            f"- Promoted: `{promotion.get('promoted')}`",
            "",
            "## Reviewer Output",
            "",
            "```json",
            json.dumps(review, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## Manual Review Checklist",
            "",
            "- Verify source references are enough for every architecture finding.",
            "- Verify vendor-specific behavior has not been flattened.",
            "- Verify recursive self-adaptation impact is handled or explicitly blocked.",
            "- Verify tests, docs, examples, and migration notes match public API changes.",
            "- Confirm no auto-merge or unsupported credential scraping was used.",
            "",
        ]
    )


def _one_line(value: object, *, limit: int = 560) -> str:
    return " ".join(str(value).split())[:limit]
