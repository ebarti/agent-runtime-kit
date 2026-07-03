"""Current-state baseline manifest helpers for SDK evolution runs."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.collectors import read_uv_lock_versions
from examples.sdk_evolution_agent.models import RunContext
from examples.sdk_evolution_agent.report import write_json

CURRENT_STATE_SCHEMA_VERSION = "1"
BASELINE_DIR = ".sdk-evolution"


def build_current_state(
    context: RunContext,
    *,
    promoted: bool,
    status: str,
    implementation: dict[str, Any],
) -> dict[str, Any]:
    """Build the baseline manifest for a run."""

    lockfile = context.workspace / "uv.lock"
    return {
        "schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "generated_at_run_id": context.run_id,
        "source_run_id": context.run_id,
        "commit": _git_output(context.workspace, ("git", "rev-parse", "HEAD")),
        "dirty_worktree": bool(_git_output(context.workspace, ("git", "status", "--short"))),
        "lockfile_hash": _sha256(lockfile),
        "packages": read_uv_lock_versions(lockfile),
        "artifacts": _artifact_refs(context.report_root, workspace=context.workspace),
        "promotion": {
            "promoted": promoted,
            "status": status,
            "implementation_applied": bool(implementation.get("applied")),
            "blocked_reason": str(implementation.get("blocked_reason") or ""),
        },
    }


def load_baseline(workspace: Path) -> dict[str, Any]:
    """Load and classify the tracked SDK evolution baseline."""

    path = workspace / BASELINE_DIR / "baseline.json"
    if not path.exists():
        return {"status": "missing", "path": str(path)}
    try:
        baseline = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "stale", "path": str(path), "reason": f"cannot read baseline: {exc}"}
    lockfile = workspace / "uv.lock"
    packages = read_uv_lock_versions(lockfile)
    lock_hash = _sha256(lockfile)
    if baseline.get("lockfile_sha256") == lock_hash and baseline.get("packages") == packages:
        return {"status": "current", "path": str(path), "baseline": baseline}
    return {
        "status": "stale",
        "path": str(path),
        "baseline": baseline,
        "reason": "lockfile hash or package versions differ",
    }


def promote_baseline(
    context: RunContext,
    *,
    snapshots: list[dict[str, Any]],
    current_state: dict[str, Any],
) -> dict[str, Any]:
    """Write the tracked baseline and promoted API snapshots."""

    workspace = context.workspace
    versions = read_uv_lock_versions(workspace / "uv.lock")
    mismatched = [
        snapshot
        for snapshot in snapshots
        if snapshot.get("package") in versions
        and snapshot.get("requested_version")
        and snapshot.get("observed_version")
        and snapshot.get("observed_version") != snapshot.get("requested_version")
    ]
    if mismatched:
        return {
            "promoted": False,
            "status": "baseline-promotion-refused",
            "blocked_reason": "snapshot observed_version does not match requested_version",
        }
    selected_snapshots: dict[str, dict[str, Any]] = {}
    snapshot_packages: set[str] = set()
    for snapshot in snapshots:
        package = str(snapshot.get("package") or "")
        if not package or package not in versions or snapshot.get("import_error"):
            continue
        snapshot_packages.add(package)
        if snapshot.get("observed_version") == versions[package]:
            selected_snapshots[package] = snapshot
    missing_promoted = sorted(snapshot_packages - set(selected_snapshots))
    if missing_promoted:
        return {
            "promoted": False,
            "status": "baseline-promotion-refused",
            "blocked_reason": (
                "no snapshot observed_version matches locked version for "
                + ", ".join(missing_promoted)
            ),
        }

    root = workspace / BASELINE_DIR
    snapshots_dir = root / "snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    package_snapshots: dict[str, str] = {}
    for package, snapshot in selected_snapshots.items():
        path = snapshots_dir / f"{package}.json"
        write_json(path, snapshot)
        package_snapshots[package] = _sha256(path)

    baseline = {
        "schema_version": CURRENT_STATE_SCHEMA_VERSION,
        "source_run_id": context.run_id,
        "commit": _git_output(workspace, ("git", "rev-parse", "HEAD")),
        "lockfile_sha256": _sha256(workspace / "uv.lock"),
        "packages": versions,
        "artifacts": current_state.get("artifacts", {}),
        "snapshot_sha256s": package_snapshots,
    }
    write_json(root / "baseline.json", baseline)
    return {"promoted": True, "status": "promoted", "baseline": baseline}


def _artifact_refs(report_root: Path, *, workspace: Path) -> dict[str, dict[str, str]]:
    names = (
        "evidence.json",
        "release_notes.json",
        "api_diffs.json",
        "behavior_probes.json",
        "behavior_diffs.json",
        "direction_analysis.json",
        "architecture_decision.json",
        "implementation_summary.json",
        "review.json",
        "report.md",
    )
    refs: dict[str, dict[str, str]] = {}
    for name in names:
        path = report_root / name
        if path.exists():
            refs[name] = {
                "path": _portable_path(path, workspace=workspace),
                "sha256": _sha256(path),
            }
    snapshots_dir = report_root / "api_snapshots"
    if snapshots_dir.exists():
        for path in sorted(snapshots_dir.glob("*.json")):
            refs[f"api_snapshots/{path.name}"] = {
                "path": _portable_path(path, workspace=workspace),
                "sha256": _sha256(path),
            }
    return refs


def _portable_path(path: Path, *, workspace: Path) -> str:
    try:
        return str(path.resolve().relative_to(workspace.resolve()))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    if not path.exists():
        return ""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(root: Path, command: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except Exception:
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()
