"""CLI orchestration for the local SDK evolution agent."""

from __future__ import annotations

import argparse
import re
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
        default=False,
        help=(
            "Opt in to inspecting candidate SDK versions by pip-installing and "
            "importing them in a temporary, credential-scrubbed virtualenv. This "
            "executes freshly downloaded upstream code, so it is OFF by default; "
            "without it, snapshots use the already-installed versions only."
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
    close_owned_runtime = runtime is None
    try:
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
        snapshots = _collect_snapshots(evidence, inspect_candidates=options.inspect_candidates)
        api_diffs = [to_jsonable(diff) for diff in diff_snapshot_groups(snapshots)]
        release_notes = [
            to_jsonable(item)
            for item in collect_release_notes(evidence.get("packages", []), update_versions)
        ]
        behavior = to_jsonable(
            collect_behavior_evidence(evidence.get("packages", []), update_versions)
        )
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
            status=(
                "promoted"
                if promoted
                else str(implementation.get("blocked_reason") or "skipped")
            ),
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
    finally:
        if close_owned_runtime:
            await _close_runtime(selected_runtime)


async def _close_runtime(runtime: AgentRuntime) -> None:
    close = getattr(runtime, "aclose", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result


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
    # Only stage the lockfile. The report lives under the gitignored default
    # report dir, so `git add`-ing it returned rc=1 and previously broke the
    # --draft-pr flow; its content is already embedded in the PR body above.
    paths = ("uv.lock",)
    results = [
        to_jsonable(stage_paths(root, paths, command_runner=command_runner)),
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
    if not _report_dir_committable(root, relative_report, command_runner=command_runner):
        # Skipped when the report dir is gitignored (the default location) or
        # outside the repository entirely: `git add` would fail on either, and
        # the report content is already embedded in the draft PR body, so there
        # is nothing that must be committed.
        return
    results = [
        stage_paths(root, (relative_report,), command_runner=command_runner),
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


def _report_dir_committable(
    root: Path, path: str, *, command_runner: CommandRunner | None
) -> bool:
    runner = command_runner
    if runner is None:
        from examples.sdk_evolution_agent.collectors import run_command

        runner = run_command
    # `git check-ignore` exits 0 when the path is ignored, 1 when it is not
    # ignored, and 128 when it cannot judge it (e.g. the path lies outside the
    # repository). Only a definitively not-ignored path can be staged.
    return runner(("git", "check-ignore", path), cwd=root).returncode == 1


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


def _collect_snapshots(evidence: dict[str, Any], *, inspect_candidates: bool = False) -> list[Any]:
    # Candidate inspection pip-installs and imports freshly downloaded upstream
    # code, so it only runs when explicitly opted in (--inspect-candidates). When
    # off, every snapshot uses the already-installed version via snapshot_current_api,
    # which imports nothing new.
    snapshots = []
    update_versions = _refresh_update_versions(evidence)
    refresh_preview_seen = evidence.get("refresh_preview") is not None
    for package in evidence.get("packages", []):
        if not isinstance(package, dict):
            continue
        name = str(package.get("name"))
        locked = package.get("locked_version")
        installed = package.get("installed_version")
        baseline = locked or installed
        if inspect_candidates and locked and installed and locked != installed:
            snapshots.append(snapshot_candidate_in_venv(name, str(locked)))
        else:
            snapshots.append(snapshot_current_api(name, version=baseline))
        if not inspect_candidates:
            continue
        candidate = update_versions.get(name)
        if candidate is None and not refresh_preview_seen:
            latest = package.get("latest_version")
            if latest and latest != baseline:
                candidate = str(latest)
        if candidate:
            snapshots.append(snapshot_candidate_in_venv(name, candidate))
    return snapshots


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
