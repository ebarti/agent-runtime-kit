"""Current-state baseline manifest helpers for SDK evolution runs."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.collectors import read_uv_lock_versions
from examples.sdk_evolution_agent.models import RunContext

CURRENT_STATE_SCHEMA_VERSION = "1"


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


def _artifact_refs(report_root: Path, *, workspace: Path) -> dict[str, dict[str, str]]:
    names = (
        "evidence.json",
        "release_notes.json",
        "api_diffs.json",
        "behavior_probes.json",
        "behavior_diffs.json",
        "behavior_summary.json",
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
