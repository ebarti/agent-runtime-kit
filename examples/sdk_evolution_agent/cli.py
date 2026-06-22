"""CLI orchestration for the local SDK evolution agent."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime_kit import AgentRuntime, RuntimeRegistry
from examples.sdk_evolution_agent.collectors import (
    CommandRunner,
    PypiClient,
    collect_evidence,
    run_verification_commands,
)
from examples.sdk_evolution_agent.events import JsonlEventSink
from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    RunContext,
    RunOptions,
    to_jsonable,
)
from examples.sdk_evolution_agent.pr import build_draft_pr_body, create_branch, create_draft_pr
from examples.sdk_evolution_agent.report import write_run_report
from examples.sdk_evolution_agent.snapshots import (
    diff_snapshot_groups,
    snapshot_candidate_in_venv,
    snapshot_current_api,
)
from examples.sdk_evolution_agent.stages import (
    maybe_run_implementation,
    resolve_runtime,
    run_analysis_pipeline,
)


async def main(argv: list[str] | None = None) -> int:
    """Parse CLI args and run the agent."""

    options = parse_args(argv)
    report_path = await run_agent(options)
    print(f"SDK evolution report: {report_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> RunOptions:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(description="Run the local SDK evolution agent.")
    parser.add_argument(
        "--runtime",
        default="fake",
        help="Runtime kind resolved via RuntimeRegistry.",
    )
    parser.add_argument(
        "--package",
        action="append",
        dest="packages",
        help="Vendor SDK package to inspect. Repeat to inspect multiple packages.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/sdk-evolution"),
        help="Directory where timestamped run reports are written.",
    )
    parser.add_argument(
        "--implementation-enabled",
        action="store_true",
        help="Allow gated implementation stages to edit the workspace.",
    )
    parser.add_argument(
        "--refresh-preview",
        action="store_true",
        help="Run targeted uv lock preview with freshness cutoffs removed.",
    )
    parser.add_argument(
        "--inspect-candidates",
        action="store_true",
        default=True,
        help=(
            "Inspect latest candidate SDK versions in temporary virtualenvs. "
            "Always enabled for update candidates; accepted for compatibility."
        ),
    )
    parser.add_argument("--create-branch", action="store_true", help="Create a local branch first.")
    parser.add_argument("--branch-name", help="Branch name for optional branch creation.")
    parser.add_argument("--draft-pr", action="store_true", help="Create a draft PR with gh.")
    parser.add_argument(
        "--pr-title",
        default="Adapt agent-runtime-kit to upstream SDK evolution",
        help="Draft PR title.",
    )
    args = parser.parse_args(argv)
    return RunOptions(
        workspace=Path.cwd(),
        runtime=args.runtime,
        packages=tuple(args.packages or DEFAULT_PACKAGES),
        report_dir=args.report_dir,
        implementation_enabled=args.implementation_enabled,
        refresh_preview=args.refresh_preview,
        inspect_candidates=args.inspect_candidates,
        create_branch=args.create_branch,
        branch_name=args.branch_name,
        draft_pr=args.draft_pr,
        pr_title=args.pr_title,
    )


async def run_agent(
    options: RunOptions,
    *,
    pypi_client: PypiClient | None = None,
    command_runner: CommandRunner | None = None,
    registry: RuntimeRegistry | None = None,
    runtime: AgentRuntime | None = None,
) -> Path:
    """Run the full local SDK evolution workflow."""

    options = replace(options, inspect_candidates=True)
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    report_root = (options.workspace / options.report_dir / run_id).resolve()
    event_log_path = report_root / "events.jsonl"
    event_sink = JsonlEventSink(event_log_path)
    context = RunContext(
        run_id=run_id,
        workspace=options.workspace,
        report_root=report_root,
        runtime=options.runtime,
        event_log_path=event_log_path,
        implementation_enabled=options.implementation_enabled,
        draft_pr=options.draft_pr,
        event_sink=event_sink,
    )
    selected_runtime = runtime or resolve_runtime(options.runtime, registry=registry)
    evidence = collect_evidence(
        options.workspace,
        packages=options.packages,
        include_refresh_preview=options.refresh_preview,
        pypi_client=pypi_client,
        command_runner=command_runner,
    )
    snapshots = _collect_snapshots(evidence)
    api_diffs = [to_jsonable(diff) for diff in diff_snapshot_groups(snapshots)]
    direction, architecture, review = await run_analysis_pipeline(
        selected_runtime,
        evidence=evidence,
        api_diffs=api_diffs,
        context=RunContext(
            run_id=context.run_id,
            workspace=context.workspace,
            report_root=context.report_root,
            runtime=context.runtime,
            event_log_path=context.event_log_path,
            implementation_enabled=context.implementation_enabled,
            draft_pr=context.draft_pr,
            event_sink=event_sink,
        ),
    )
    implementation = await maybe_run_implementation(
        selected_runtime,
        evidence=evidence,
        direction=direction,
        architecture=architecture,
        review=review,
        context=context,
    )
    config = to_jsonable(options)
    config["run_id"] = run_id
    config["event_log_path"] = str(context.event_log_path)
    report_path = write_run_report(
        context,
        config=config,
        evidence=evidence,
        snapshots=[to_jsonable(snapshot) for snapshot in snapshots],
        api_diffs=api_diffs,
        direction=direction,
        architecture=architecture,
        implementation=implementation,
        review=review,
    )
    optional_results_changed = False
    if options.implementation_enabled and implementation.get("applied"):
        verification_results = run_verification_commands(
            options.workspace,
            tuple(str(item) for item in architecture.get("verification_commands", [])),
            command_runner=command_runner,
        )
        implementation.setdefault("verification_results", []).extend(
            to_jsonable(verification_results)
        )
        optional_results_changed = True
    if options.create_branch and options.branch_name:
        branch_result = create_branch(
            options.workspace,
            options.branch_name,
            command_runner=command_runner,
        )
        implementation.setdefault("verification_results", []).append(to_jsonable(branch_result))
        optional_results_changed = True
    if options.draft_pr:
        body = build_draft_pr_body(report_path.read_text(encoding="utf-8"))
        pr_result = create_draft_pr(
            options.workspace,
            title=options.pr_title,
            body=body,
            command_runner=command_runner,
        )
        implementation.setdefault("verification_results", []).append(to_jsonable(pr_result))
        optional_results_changed = True
    else:
        body = None
    if optional_results_changed:
        report_path = write_run_report(
            context,
            config=config,
            evidence=evidence,
            snapshots=[to_jsonable(snapshot) for snapshot in snapshots],
            api_diffs=api_diffs,
            direction=direction,
            architecture=architecture,
            implementation=implementation,
            review=review,
            pr_body=body,
        )
    return report_path


def _collect_snapshots(evidence: dict[str, Any], *, inspect_candidates: bool = True) -> list[Any]:
    del inspect_candidates  # Candidate inspection is mandatory for update candidates.
    snapshots = []
    for package in evidence.get("packages", []):
        if not isinstance(package, dict):
            continue
        name = str(package.get("name"))
        snapshots.append(snapshot_current_api(name, version=package.get("installed_version")))
        latest = package.get("latest_version")
        baseline = package.get("locked_version") or package.get("installed_version")
        if latest and latest != baseline:
            snapshots.append(snapshot_candidate_in_venv(name, str(latest)))
    return snapshots
