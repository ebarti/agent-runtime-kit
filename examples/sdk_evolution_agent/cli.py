"""CLI orchestration for the local SDK evolution agent."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_runtime_kit import AgentRuntime, RuntimeRegistry
from examples.sdk_evolution_agent.behavior import collect_behavior_evidence
from examples.sdk_evolution_agent.collectors import (
    CommandRunner,
    PypiClient,
    collect_evidence,
    run_lock_update,
    run_verification_commands,
)
from examples.sdk_evolution_agent.current_state import build_current_state
from examples.sdk_evolution_agent.events import JsonlEventSink
from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    ApiMember,
    ApiSnapshot,
    RunContext,
    RunOptions,
    to_jsonable,
)
from examples.sdk_evolution_agent.pr import (
    build_draft_pr_body,
    commit_staged,
    create_branch,
    create_draft_pr,
    push_branch,
    stage_paths,
)
from examples.sdk_evolution_agent.release_notes import collect_release_notes
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

DEFAULT_VERIFICATION_COMMANDS = (
    "uv run ruff check .",
    "uv run mypy",
    "uv run pytest",
    "uv lock --check",
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
    parser.add_argument("--pr-base", help="Base branch for optional draft PR creation.")
    parser.add_argument(
        "--commit-message",
        default="Run SDK evolution update",
        help="Commit message for optional autonomous SDK update PR.",
    )
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
        pr_base=args.pr_base,
        commit_message=args.commit_message,
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
    pre_run_results: list[dict[str, Any]] = []
    if options.create_branch and options.branch_name:
        branch_result = create_branch(
            options.workspace,
            options.branch_name,
            command_runner=command_runner,
        )
        pre_run_results.append(to_jsonable(branch_result))
        if branch_result.returncode != 0:
            raise RuntimeError(
                f"failed to create branch {options.branch_name}: {branch_result.stderr}"
            )
    evidence = collect_evidence(
        options.workspace,
        packages=options.packages,
        include_refresh_preview=options.refresh_preview,
        pypi_client=pypi_client,
        command_runner=command_runner,
    )
    update_versions = _refresh_update_versions(evidence)
    snapshots = _collect_snapshots(evidence, workspace=options.workspace)
    api_diffs = [to_jsonable(diff) for diff in diff_snapshot_groups(snapshots)]
    release_notes = [
        to_jsonable(item)
        for item in collect_release_notes(evidence.get("packages", []), update_versions)
    ]
    behavior = to_jsonable(collect_behavior_evidence(evidence.get("packages", []), update_versions))
    direction, architecture, review = await run_analysis_pipeline(
        selected_runtime,
        evidence=evidence,
        api_diffs=api_diffs,
        release_notes=release_notes,
        behavior=behavior,
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
    implementation.setdefault("verification_results", []).extend(pre_run_results)
    config = to_jsonable(options)
    config["run_id"] = run_id
    config["event_log_path"] = str(context.event_log_path)

    if options.implementation_enabled and implementation.get("allowed"):
        implementation = _run_local_sdk_update(
            options,
            update_versions=update_versions,
            implementation=implementation,
            command_runner=command_runner,
        )

    promoted = bool(implementation.get("applied")) and _verification_passed(implementation)
    current_state: dict[str, Any] = {
        "promotion": {
            "promoted": False,
            "status": "pending-report-write",
        }
    }
    report_path = _write_full_report(
        context,
        config=config,
        evidence=evidence,
        snapshots=[to_jsonable(snapshot) for snapshot in snapshots],
        api_diffs=api_diffs,
        release_notes=release_notes,
        behavior=behavior,
        current_state=current_state,
        direction=direction,
        architecture=architecture,
        implementation=implementation,
        review=review,
    )
    current_state = build_current_state(
        context,
        promoted=promoted,
        status="promoted" if promoted else str(implementation.get("blocked_reason") or "skipped"),
        implementation=implementation,
    )
    report_path = _write_full_report(
        context,
        config=config,
        evidence=evidence,
        snapshots=[to_jsonable(snapshot) for snapshot in snapshots],
        api_diffs=api_diffs,
        release_notes=release_notes,
        behavior=behavior,
        current_state=current_state,
        direction=direction,
        architecture=architecture,
        implementation=implementation,
        review=review,
    )

    if options.draft_pr:
        git_results = _create_autonomous_pr(
            options.workspace,
            report_path=report_path,
            options=options,
            command_runner=command_runner,
        )
        implementation.setdefault("verification_results", []).extend(git_results)
        report_path = _write_full_report(
            context,
            config=config,
            evidence=evidence,
            snapshots=[to_jsonable(snapshot) for snapshot in snapshots],
            api_diffs=api_diffs,
            release_notes=release_notes,
            behavior=behavior,
            current_state=current_state,
            direction=direction,
            architecture=architecture,
            implementation=implementation,
            review=review,
        )
        _commit_final_autonomous_pr_report(
            options.workspace,
            report_path=report_path,
            options=options,
            command_runner=command_runner,
        )
    return report_path


def _run_local_sdk_update(
    options: RunOptions,
    *,
    update_versions: dict[str, str],
    implementation: dict[str, Any],
    command_runner: CommandRunner | None,
) -> dict[str, Any]:
    packages = tuple(sorted(update_versions))
    if not packages:
        return {
            **implementation,
            "applied": False,
            "blocked_reason": "no resolver-selected SDK updates",
        }
    update_result = run_lock_update(
        options.workspace,
        packages,
        command_runner=command_runner,
    )
    results = list(implementation.get("verification_results") or [])
    results.append(to_jsonable(update_result))
    applied = update_result.returncode == 0
    changes = list(implementation.get("changes") or [])
    if applied:
        changes.append("Updated uv.lock for resolver-selected SDK packages: " + ", ".join(packages))
        verification_commands = tuple(DEFAULT_VERIFICATION_COMMANDS)
        verification_results = run_verification_commands(
            options.workspace,
            verification_commands,
            command_runner=command_runner,
        )
        results.extend(to_jsonable(verification_results))
    return {
        **implementation,
        "applied": applied,
        "changes": changes,
        "verification_results": results,
        "blocked_reason": "" if applied else update_result.stderr or update_result.stdout,
    }


def _write_full_report(
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
    return write_run_report(
        context,
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
        pr_body=pr_body,
    )


def _verification_passed(implementation: dict[str, Any]) -> bool:
    results = implementation.get("verification_results")
    if not isinstance(results, list):
        return False
    command_results = [item for item in results if isinstance(item, dict) and "returncode" in item]
    return bool(command_results) and all(
        int(item.get("returncode", 1)) == 0 for item in command_results
    )


def _create_autonomous_pr(
    root: Path,
    *,
    report_path: Path,
    options: RunOptions,
    command_runner: CommandRunner | None,
) -> list[dict[str, Any]]:
    branch_name = options.branch_name or _current_branch(root, command_runner=command_runner)
    body = build_draft_pr_body(report_path.read_text(encoding="utf-8"))
    relative_report = _relative_path(root, report_path.parent)
    paths = ("uv.lock", relative_report)
    results = [
        to_jsonable(stage_paths(root, paths, force=True, command_runner=command_runner)),
        to_jsonable(
            commit_staged(
                root,
                message=options.commit_message,
                command_runner=command_runner,
            )
        ),
    ]
    if branch_name:
        results.append(
            to_jsonable(push_branch(root, branch_name=branch_name, command_runner=command_runner))
        )
    results.append(
        to_jsonable(
            create_draft_pr(
                root,
                title=options.pr_title,
                body=body,
                base=options.pr_base,
                head=branch_name,
                command_runner=command_runner,
            )
        )
    )
    return results


def _commit_final_autonomous_pr_report(
    root: Path,
    *,
    report_path: Path,
    options: RunOptions,
    command_runner: CommandRunner | None,
) -> None:
    branch_name = options.branch_name or _current_branch(root, command_runner=command_runner)
    relative_report = _relative_path(root, report_path.parent)
    results = [
        stage_paths(root, (relative_report,), force=True, command_runner=command_runner),
        commit_staged(
            root,
            message="Finalize SDK evolution report",
            command_runner=command_runner,
        ),
    ]
    if branch_name:
        results.append(push_branch(root, branch_name=branch_name, command_runner=command_runner))
    failed = [result for result in results if result.returncode != 0]
    if failed:
        detail = failed[0].stderr or failed[0].stdout
        raise RuntimeError(f"failed to commit final autonomous PR report: {detail}")


def _current_branch(root: Path, *, command_runner: CommandRunner | None) -> str:
    runner = command_runner or None
    if runner is None:
        from examples.sdk_evolution_agent.collectors import run_command

        runner = run_command
    result = runner(("git", "branch", "--show-current"), cwd=root)
    return result.stdout.strip() if result.returncode == 0 else ""


def _relative_path(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _collect_snapshots(
    evidence: dict[str, Any],
    *,
    inspect_candidates: bool = True,
    workspace: Path | None = None,
) -> list[Any]:
    del inspect_candidates  # Candidate inspection is mandatory for update candidates.
    snapshots = []
    update_versions = _refresh_update_versions(evidence)
    refresh_preview_seen = evidence.get("refresh_preview") is not None
    baseline_artifacts = _load_promoted_baseline_snapshots(workspace) if workspace else {}
    for package in evidence.get("packages", []):
        if not isinstance(package, dict):
            continue
        name = str(package.get("name"))
        locked = package.get("locked_version")
        installed = package.get("installed_version")
        baseline = locked or installed
        if locked and installed and locked != installed:
            snapshots.append(snapshot_candidate_in_venv(name, str(locked)))
        else:
            current_snapshot = snapshot_current_api(name, version=baseline)
            if current_snapshot.import_error and baseline:
                current_snapshot = baseline_artifacts.get((name, str(baseline)), current_snapshot)
            snapshots.append(current_snapshot)
        candidate = update_versions.get(name)
        if candidate is None and not refresh_preview_seen:
            latest = package.get("latest_version")
            if latest and latest != baseline:
                candidate = str(latest)
        if candidate:
            snapshots.append(snapshot_candidate_in_venv(name, candidate))
    return snapshots


def _load_promoted_baseline_snapshots(workspace: Path | None) -> dict[tuple[str, str], ApiSnapshot]:
    if workspace is None:
        return {}
    manifests = _promoted_current_state_manifests(workspace)
    snapshots: dict[tuple[str, str], ApiSnapshot] = {}
    for manifest in manifests:
        artifacts = manifest.get("artifacts")
        if not isinstance(artifacts, dict):
            continue
        for name, ref in sorted(artifacts.items()):
            if not name.startswith("api_snapshots/") or not isinstance(ref, dict):
                continue
            path_value = ref.get("path")
            if not isinstance(path_value, str) or not path_value:
                continue
            path = Path(path_value)
            if not path.is_absolute():
                path = workspace / path
            snapshot = _load_api_snapshot(path)
            if snapshot is None or snapshot.import_error or not snapshot.version:
                continue
            snapshots[(snapshot.package, str(snapshot.version))] = snapshot
    return snapshots


def _promoted_current_state_manifests(workspace: Path) -> list[dict[str, Any]]:
    manifests: list[dict[str, Any]] = []
    for path in sorted((workspace / "reports").glob("sdk-evolution*/**/current_state.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        promotion = data.get("promotion")
        if isinstance(promotion, dict) and promotion.get("promoted") is True:
            manifests.append(data)
    manifests.sort(key=lambda item: str(item.get("generated_at_run_id") or ""))
    return manifests


def _load_api_snapshot(path: Path) -> ApiSnapshot | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    members = tuple(
        ApiMember(
            name=str(item.get("name") or ""),
            kind=str(item.get("kind") or ""),
            signature=str(item.get("signature") or ""),
            module=str(item.get("module") or ""),
        )
        for item in data.get("members", ())
        if isinstance(item, dict) and item.get("name")
    )
    return ApiSnapshot(
        package=str(data.get("package") or ""),
        version=str(data.get("version")) if data.get("version") is not None else None,
        module=str(data.get("module") or ""),
        members=members,
        import_error=data.get("import_error"),
        source="current-state-artifact",
    )


def _refresh_update_versions(evidence: dict[str, Any]) -> dict[str, str]:
    preview = evidence.get("refresh_preview")
    if not isinstance(preview, dict):
        return {}
    text = f"{preview.get('stdout') or ''}\n{preview.get('stderr') or ''}"
    return {
        package: version
        for package, version in re.findall(
            r"Update\s+([A-Za-z0-9_.-]+)\s+v\S+\s+->\s+v(\S+)",
            text,
        )
    }
