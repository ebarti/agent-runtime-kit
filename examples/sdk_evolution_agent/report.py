"""Report generation for SDK evolution runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

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
    write_json(context.report_root / "api_diffs.json", api_diffs)
    write_json(context.report_root / "direction_analysis.json", direction)
    write_json(context.report_root / "architecture_decision.json", architecture)
    write_json(context.report_root / "implementation_summary.json", implementation)
    write_json(context.report_root / "review.json", review)
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
            api_diffs=api_diffs,
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
    api_diffs: list[dict[str, Any]],
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
            "## API Diffs",
            "",
            f"- Diff count: `{len(api_diffs)}`",
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
