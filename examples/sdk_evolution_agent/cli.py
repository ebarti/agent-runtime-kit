"""CLI orchestration for the local SDK evolution agent."""

from __future__ import annotations

import argparse
import sys
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
from examples.sdk_evolution_agent.current_state import (
    build_current_state,
    load_baseline,
    promote_baseline,
)
from examples.sdk_evolution_agent.events import JsonlEventSink
from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    CommandResult,
    RunContext,
    RunOptions,
    to_jsonable,
)
from examples.sdk_evolution_agent.pr import (
    add_label_to_pr,
    build_draft_pr_body,
    close_pr,
    comment_pr,
    commit_staged,
    create_branch,
    create_draft_pr,
    ensure_label,
    list_open_sdk_evolution_prs,
    parse_pr_marker,
    push_branch,
    stage_paths,
)
from examples.sdk_evolution_agent.preflight import PreflightError, validate_run_plan
from examples.sdk_evolution_agent.release_notes import collect_release_notes
from examples.sdk_evolution_agent.report import write_run_report
from examples.sdk_evolution_agent.resolver import candidate_map, raise_package_cap_in_workspace
from examples.sdk_evolution_agent.snapshots import (
    diff_snapshot_groups,
    snapshot_candidate_in_venv,
    snapshot_current_api,
)
from examples.sdk_evolution_agent.stages import (
    maybe_run_implementation,
    resolve_runtime,
    run_analysis_pipeline,
    run_implementation_review_stage,
    run_implementation_stage,
    with_cap_raise_guard,
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
    try:
        report_path = await run_agent(options)
    except PreflightError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"SDK evolution report: {report_path}")
    return 0


def parse_args(argv: list[str] | None = None) -> RunOptions:
    """Parse command-line options."""

    parser = argparse.ArgumentParser(description="Run the local SDK evolution agent.")
    parser.add_argument(
        "--mode",
        choices=("report", "upgrade", "upgrade-pr"),
        help="Apply a tested flag bundle: report, upgrade, or upgrade-pr.",
    )
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
        "--allow-dirty",
        action="store_true",
        help="Dev-only escape hatch: allow implementation from a dirty worktree.",
    )
    parser.add_argument(
        "--allow-cap-raise",
        action="store_true",
        help="Allow guarded pyproject upper-bound raises for beyond-cap candidates.",
    )
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
    run_id = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mode = args.mode
    refresh_preview = args.refresh_preview or mode in {"report", "upgrade", "upgrade-pr"}
    inspect_candidates = args.inspect_candidates or mode in {"upgrade", "upgrade-pr"}
    implementation_enabled = args.implementation_enabled or mode in {"upgrade", "upgrade-pr"}
    allow_cap_raise = args.allow_cap_raise or mode in {"upgrade", "upgrade-pr"}
    create_branch = args.create_branch or mode == "upgrade-pr"
    draft_pr = args.draft_pr or mode == "upgrade-pr"
    branch_name = args.branch_name
    if mode == "upgrade-pr" and not branch_name:
        branch_name = f"sdk-evolution/{run_id}"
    return RunOptions(
        workspace=Path.cwd(),
        runtime=args.runtime,
        mode=mode,
        packages=tuple(args.packages or DEFAULT_PACKAGES),
        report_dir=args.report_dir,
        implementation_enabled=implementation_enabled,
        refresh_preview=refresh_preview,
        inspect_candidates=inspect_candidates,
        create_branch=create_branch,
        branch_name=branch_name,
        draft_pr=draft_pr,
        pr_base=args.pr_base or ("main" if mode == "upgrade-pr" else None),
        allow_dirty=args.allow_dirty,
        allow_cap_raise=allow_cap_raise,
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
    violations = validate_run_plan(options, options.workspace, command_runner=command_runner)
    if violations:
        raise PreflightError(violations)
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
        evidence["baseline"] = load_baseline(options.workspace)
        update_versions = candidate_map(evidence.get("update_candidates", []))
        beyond_versions = candidate_map(evidence.get("update_candidates_beyond_cap", []))
        candidate_versions = {**update_versions, **beyond_versions}
        snapshots = _collect_snapshots(evidence, inspect_candidates=options.inspect_candidates)
        _record_snapshot_uncertainty(evidence, snapshots)
        api_diffs = [to_jsonable(diff) for diff in diff_snapshot_groups(snapshots)]
        release_notes = [
            to_jsonable(item)
            for item in collect_release_notes(evidence.get("packages", []), candidate_versions)
        ]
        behavior = to_jsonable(
            collect_behavior_evidence(
                evidence.get("packages", []),
                candidate_versions,
                inspect_candidates=options.inspect_candidates,
            )
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
        implementation.setdefault("setup_results", []).extend(pre_run_results)
        config = to_jsonable(options)
        config["run_id"] = run_id
        config["event_log_path"] = str(context.event_log_path)

        if options.implementation_enabled and implementation.get("allowed"):
            implementation = await _run_local_sdk_update(
                options,
                update_versions=update_versions,
                beyond_candidates=evidence.get("update_candidates_beyond_cap", []),
                implementation=implementation,
                runtime=selected_runtime,
                context=context,
                evidence=evidence,
                architecture=architecture,
                review=review,
                api_diffs=api_diffs,
                release_notes=release_notes,
                behavior=behavior,
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
        snapshots_json = [to_jsonable(snapshot) for snapshot in snapshots]
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
        if promoted:
            promotion = promote_baseline(
                context,
                snapshots=snapshots_json,
                current_state=current_state,
            )
            current_state["promotion"] = {
                **current_state.get("promotion", {}),
                **promotion,
            }
            if not promotion.get("promoted"):
                promoted = False
                implementation["applied"] = False
                implementation["blocked_reason"] = str(promotion.get("blocked_reason") or "")
        report_path = _write_full_report(
            context,
            config=config,
            evidence=evidence,
            snapshots=snapshots_json,
            api_diffs=api_diffs,
            release_notes=release_notes,
            behavior=behavior,
            current_state=current_state,
            direction=direction,
            architecture=architecture,
            implementation=implementation,
            review=review,
        )

        if (
            options.draft_pr
            and implementation.get("applied")
            and _verification_passed(implementation)
        ):
            git_results = _create_autonomous_pr(
                options.workspace,
                report_path=report_path,
                options=options,
                package_versions={**update_versions, **_applied_cap_raise_versions(implementation)},
                run_id=run_id,
                command_runner=command_runner,
            )
            implementation.setdefault("pr_results", []).extend(git_results)
            for item in git_results:
                if isinstance(item, dict) and item.get("pr_skipped_reason"):
                    implementation["pr_skipped_reason"] = item["pr_skipped_reason"]
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
        elif options.draft_pr:
            implementation["pr_skipped_reason"] = (
                "implementation was not applied or verification did not pass"
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


async def _run_local_sdk_update(
    options: RunOptions,
    *,
    update_versions: dict[str, str],
    beyond_candidates: Any,
    implementation: dict[str, Any],
    runtime: AgentRuntime,
    context: RunContext,
    evidence: dict[str, Any],
    architecture: dict[str, Any],
    review: dict[str, Any],
    api_diffs: list[dict[str, Any]],
    release_notes: list[dict[str, Any]],
    behavior: dict[str, Any],
    command_runner: CommandRunner | None,
) -> dict[str, Any]:
    root = options.workspace
    transaction_files = _snapshot_transaction_files(root)
    allowed_file_snapshot = _snapshot_allowed_files(root)
    apply_results = list(implementation.get("apply_results") or [])
    verification_results: list[dict[str, Any]] = []
    changes = list(implementation.get("changes") or [])
    cap_raise_versions: dict[str, str] = {}
    packages = set(update_versions)

    if options.allow_cap_raise:
        for candidate in beyond_candidates if isinstance(beyond_candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            gate = with_cap_raise_guard(
                architecture,
                candidate=candidate,
                api_diffs=api_diffs,
                release_notes=release_notes,
                behavior=behavior,
                review=review,
            )
            if not gate.allowed:
                continue
            package = str(candidate["package"])
            version = str(candidate["to_version"])
            raised = raise_package_cap_in_workspace(root, package, version)
            if raised is not None:
                cap_raise_versions[package] = version
                packages.add(package)
                changes.append(
                    f"Raised {package} upper bound {raised.current} -> {raised.replacement}"
                )

    if not packages:
        return {
            **implementation,
            "applied": False,
            "blocked_reason": "no resolver-selected SDK updates",
        }
    try:
        update_result = run_lock_update(
            root, tuple(sorted(packages)), command_runner=command_runner
        )
        apply_results.append(to_jsonable(update_result))
        if update_result.returncode != 0:
            _restore_transaction(root, transaction_files, allowed_file_snapshot)
            return {
                **implementation,
                "applied": False,
                "rolled_back": True,
                "changes": changes,
                "apply_results": apply_results,
                "verification_results": verification_results,
                "cap_raise_versions": cap_raise_versions,
                "blocked_reason": update_result.stderr or update_result.stdout,
            }

        changes.append(
            "Updated uv.lock for resolver-selected SDK packages: " + ", ".join(sorted(packages))
        )
        if _needs_ai_implementation(architecture):
            stage_output = await run_implementation_stage(
                runtime,
                payload={
                    "evidence": evidence,
                    "architecture_decision": architecture,
                    "review": review,
                    "allowed_roots": sorted(_ALLOWED_IMPLEMENTATION_PREFIXES),
                },
                context=context,
            )
            changes.extend(str(item) for item in stage_output.get("changes", []))
            if stage_output.get("blocked_reason"):
                _restore_transaction(root, transaction_files, allowed_file_snapshot)
                return {
                    **implementation,
                    "applied": False,
                    "rolled_back": True,
                    "changes": changes,
                    "apply_results": apply_results,
                    "verification_results": verification_results,
                    "cap_raise_versions": cap_raise_versions,
                    "blocked_reason": str(stage_output["blocked_reason"]),
                }

        status = _git_status(root, command_runner=command_runner)
        changed_paths = _changed_paths(status)
        out_of_scope = [
            path for path in changed_paths if not _implementation_path_allowed(path)
        ]
        if out_of_scope:
            _restore_transaction(root, transaction_files, allowed_file_snapshot)
            return {
                **implementation,
                "applied": False,
                "rolled_back": True,
                "changes": changes,
                "apply_results": apply_results,
                "verification_results": verification_results,
                "cap_raise_versions": cap_raise_versions,
                "blocked_reason": "implementation changed files outside allowed scope: "
                + ", ".join(out_of_scope),
            }

        verification_commands = _verification_commands(architecture)
        try:
            verification = run_verification_commands(
                root,
                verification_commands,
                command_runner=command_runner,
            )
            verification_results.extend(to_jsonable(verification))
        except Exception as exc:
            verification_results.append(
                to_jsonable(
                    CommandResult(
                        command=("verification",),
                        returncode=1,
                        stderr=str(exc),
                    )
                )
            )
        candidate = {
            **implementation,
            "applied": True,
            "rolled_back": False,
            "changes": changes,
            "apply_results": apply_results,
            "verification_results": verification_results,
            "cap_raise_versions": cap_raise_versions,
            "blocked_reason": "",
        }
        if not _verification_passed(candidate):
            _restore_transaction(root, transaction_files, allowed_file_snapshot)
            return {
                **candidate,
                "applied": False,
                "rolled_back": True,
                "blocked_reason": "verification failed; workspace restored",
            }

        diff = _git_diff(root, command_runner=command_runner)
        implementation_review = await run_implementation_review_stage(
            runtime,
            payload={
                "changed_files": changed_paths,
                "diff": diff[:65536],
                "verification_results": verification_results,
            },
            context=context,
        )
        candidate["implementation_review"] = implementation_review
        if implementation_review.get("status") != "pass":
            _restore_transaction(root, transaction_files, allowed_file_snapshot)
            return {
                **candidate,
                "applied": False,
                "rolled_back": True,
                "blocked_reason": "implementation reviewer rejected the diff",
            }
        return candidate
    except Exception as exc:
        _restore_transaction(root, transaction_files, allowed_file_snapshot)
        return {
            **implementation,
            "applied": False,
            "rolled_back": True,
            "changes": changes,
            "apply_results": apply_results,
            "verification_results": verification_results,
            "cap_raise_versions": cap_raise_versions,
            "blocked_reason": f"implementation failed; workspace restored: {exc}",
        }


_ALLOWED_IMPLEMENTATION_PREFIXES = (
    "examples/sdk_evolution_agent/",
    "tests/test_sdk_evolution_",
    "docs/sdk-evolution-agent",
    ".sdk-evolution/",
)
_ALLOWED_IMPLEMENTATION_FILES = {"uv.lock", "pyproject.toml"}


def _verification_commands(architecture: dict[str, Any]) -> tuple[str, ...]:
    commands = list(DEFAULT_VERIFICATION_COMMANDS)
    for command in architecture.get("verification_commands") or []:
        if isinstance(command, str) and command not in commands:
            commands.append(command)
    return tuple(commands)


def _snapshot_transaction_files(root: Path) -> dict[str, bytes | None]:
    snapshot: dict[str, bytes | None] = {}
    for name in ("uv.lock", "pyproject.toml"):
        path = root / name
        snapshot[name] = path.read_bytes() if path.exists() else None
    return snapshot


def _snapshot_allowed_files(root: Path) -> dict[str, bytes]:
    snapshot: dict[str, bytes] = {}
    for base in (root / "examples" / "sdk_evolution_agent", root / "docs", root / "tests"):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                rel = _relative_path(root, path)
                if _implementation_path_allowed(rel):
                    snapshot[rel] = path.read_bytes()
    return snapshot


def _restore_transaction(
    root: Path,
    transaction_files: dict[str, bytes | None],
    allowed_file_snapshot: dict[str, bytes],
) -> None:
    for relative, data in transaction_files.items():
        path = root / relative
        if data is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(data)
    for relative, data in allowed_file_snapshot.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for relative in _changed_paths(_git_status(root, command_runner=None)):
        if relative.startswith("reports/"):
            continue
        if relative in transaction_files or relative in allowed_file_snapshot:
            continue
        path = root / relative
        if path.exists() and _implementation_path_allowed(relative):
            if path.is_dir():
                for child in sorted(path.rglob("*"), reverse=True):
                    if child.is_file() or child.is_symlink():
                        child.unlink(missing_ok=True)
                    elif child.is_dir():
                        child.rmdir()
                path.rmdir()
            else:
                path.unlink(missing_ok=True)


def _git_status(root: Path, *, command_runner: CommandRunner | None) -> str:
    runner = command_runner
    if runner is None:
        from examples.sdk_evolution_agent.collectors import run_command

        runner = run_command
    result = runner(("git", "status", "--porcelain"), cwd=root)
    return result.stdout if result.returncode == 0 else ""


def _git_diff(root: Path, *, command_runner: CommandRunner | None) -> str:
    runner = command_runner
    if runner is None:
        from examples.sdk_evolution_agent.collectors import run_command

        runner = run_command
    result = runner(("git", "diff", "--", "."), cwd=root)
    return result.stdout if result.returncode == 0 else ""


def _changed_paths(status: str) -> list[str]:
    paths: list[str] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def _implementation_path_allowed(path: str) -> bool:
    return path in _ALLOWED_IMPLEMENTATION_FILES or any(
        path.startswith(prefix) for prefix in _ALLOWED_IMPLEMENTATION_PREFIXES
    )


def _needs_ai_implementation(architecture: dict[str, Any]) -> bool:
    items = list(architecture.get("self_adaptation_plan") or [])
    items.extend(architecture.get("docs_test_changes") or [])
    return any(_implementation_path_allowed(str(item)) for item in items)


def _applied_cap_raise_versions(implementation: dict[str, Any]) -> dict[str, str]:
    versions = implementation.get("cap_raise_versions")
    if not isinstance(versions, dict):
        return {}
    return {str(package): str(version) for package, version in versions.items()}


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
    package_versions: dict[str, str],
    run_id: str,
    command_runner: CommandRunner | None,
) -> list[dict[str, Any]]:
    branch_name = options.branch_name or _current_branch(root, command_runner=command_runner)
    body = build_draft_pr_body(
        report_path.read_text(encoding="utf-8"),
        package_versions=package_versions,
        run_id=run_id,
    )
    results: list[dict[str, Any]] = []
    superseded: list[int] = []
    for item in list_open_sdk_evolution_prs(root, command_runner=command_runner):
        marker = parse_pr_marker(str(item.get("body") or ""))
        if marker is None:
            continue
        number = int(item.get("number") or 0)
        if marker.get("packages") == package_versions and number:
            comment = comment_pr(
                root,
                number,
                f"New SDK evolution run `{run_id}` produced the same package delta. "
                f"Report: {report_path}",
                command_runner=command_runner,
            )
            results.append(to_jsonable(comment))
            _raise_on_failed_results(results, "comment on identical SDK evolution PR")
            results.append({"pr_skipped_reason": f"identical delta: #{number}"})
            return results
        if number:
            superseded.append(number)

    paths = ("uv.lock", "pyproject.toml", ".sdk-evolution")
    results.extend(
        [
            to_jsonable(stage_paths(root, paths, command_runner=command_runner)),
            to_jsonable(
                commit_staged(
                    root,
                    message=options.commit_message,
                    command_runner=command_runner,
                )
            ),
        ]
    )
    if branch_name:
        results.append(
            to_jsonable(push_branch(root, branch_name=branch_name, command_runner=command_runner))
        )
    results.append(to_jsonable(ensure_label(root, "sdk-evolution", command_runner=command_runner)))
    create_result = create_draft_pr(
        root,
        title=options.pr_title,
        body=body,
        base=options.pr_base,
        head=branch_name,
        command_runner=command_runner,
    )
    results.append(to_jsonable(create_result))
    _raise_on_failed_results(results, "create SDK evolution draft PR")
    pr_ref = (create_result.stdout.strip().splitlines() or [branch_name or ""])[0]
    if pr_ref:
        results.append(
            to_jsonable(
                add_label_to_pr(root, pr_ref, "sdk-evolution", command_runner=command_runner)
            )
        )
    _raise_on_failed_results(results, "label SDK evolution draft PR")
    for number in superseded:
        results.append(
            to_jsonable(
                close_pr(
                    root,
                    number,
                    f"Superseded by SDK evolution run `{run_id}` ({pr_ref}).",
                    command_runner=command_runner,
                )
            )
        )
    _raise_on_failed_results(results, "supersede old SDK evolution PRs")
    return results


def _raise_on_failed_results(results: list[dict[str, Any]], step: str) -> None:
    for result in results:
        if "returncode" not in result:
            continue
        if int(result.get("returncode", 1)) != 0:
            detail = result.get("stderr") or result.get("stdout") or result.get("command")
            raise RuntimeError(f"failed to {step}: {detail}")


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
    update_versions = {
        **candidate_map(evidence.get("update_candidates", [])),
        **candidate_map(evidence.get("update_candidates_beyond_cap", [])),
    }
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
        if candidate:
            snapshots.append(snapshot_candidate_in_venv(name, candidate))
    return snapshots


def _record_snapshot_uncertainty(evidence: dict[str, Any], snapshots: list[Any]) -> None:
    mismatches = []
    for snapshot in snapshots:
        requested = getattr(snapshot, "requested_version", None)
        observed = getattr(snapshot, "observed_version", None)
        if requested and observed and requested != observed:
            mismatches.append(f"{snapshot.package}: requested {requested}, observed {observed}")
    if mismatches:
        evidence.setdefault("uncertainty", []).append(
            "Snapshot provenance mismatches: " + "; ".join(mismatches)
        )
