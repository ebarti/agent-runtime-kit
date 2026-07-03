"""API snapshot and diff helpers for upstream SDK inspection."""

from __future__ import annotations

import importlib
import importlib.metadata
import inspect
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.models import ApiDiff, ApiMember, ApiSnapshot

DEFAULT_MODULES = {
    "claude-agent-sdk": "claude_agent_sdk",
    "openai-codex": "openai_codex",
    "openai-codex-cli-bin": "openai_codex_cli_bin",
    "google-antigravity": "google.antigravity",
}


def snapshot_current_api(package: str, *, version: str | None = None) -> ApiSnapshot:
    """Capture public API for a package importable in the current environment."""

    module_name = DEFAULT_MODULES.get(package, package.replace("-", "_"))
    observed = _observed_version(package)
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return ApiSnapshot(
            package=package,
            version=version,
            module=module_name,
            requested_version=version,
            observed_version=observed,
            provenance=_provenance(version, observed),
            import_error=str(exc),
        )
    members: list[ApiMember] = []
    for name, value in inspect.getmembers(module):
        if name.startswith("_"):
            continue
        members.append(
            ApiMember(
                name=name,
                kind=_member_kind(value),
                signature=_signature(value),
                module=str(getattr(value, "__module__", "")),
            )
        )
    return ApiSnapshot(
        package=package,
        version=version or observed or str(getattr(module, "__version__", "") or ""),
        module=module_name,
        requested_version=version,
        observed_version=observed,
        provenance=_provenance(version, observed),
        members=tuple(sorted(members, key=lambda item: item.name)),
    )


def diff_snapshots(before: ApiSnapshot, after: ApiSnapshot) -> ApiDiff:
    """Diff two public API snapshots."""

    before_members = {member.name: member for member in before.members}
    after_members = {member.name: member for member in after.members}
    added = tuple(sorted(set(after_members) - set(before_members)))
    removed = tuple(sorted(set(before_members) - set(after_members)))
    changed = tuple(
        sorted(
            name
            for name in set(before_members) & set(after_members)
            if before_members[name].signature != after_members[name].signature
            or before_members[name].kind != after_members[name].kind
        )
    )
    return ApiDiff(
        package=before.package,
        from_version=before.version,
        to_version=after.version,
        added=added,
        removed=removed,
        changed=changed,
    )


def diff_snapshot_groups(snapshots: Sequence[ApiSnapshot]) -> tuple[ApiDiff, ...]:
    """Diff adjacent snapshots grouped by package."""

    diffs: list[ApiDiff] = []
    grouped: dict[str, list[ApiSnapshot]] = {}
    for snapshot in snapshots:
        if snapshot.import_error:
            continue
        grouped.setdefault(snapshot.package, []).append(snapshot)
    for group in grouped.values():
        for index in range(1, len(group)):
            diffs.append(diff_snapshots(group[index - 1], group[index]))
    return tuple(diffs)


def isolated_env(home: Path) -> dict[str, str]:
    """A minimal environment for candidate subprocesses: PATH + a throwaway HOME.

    Deliberately omits the caller's credentials/config so freshly downloaded
    upstream code executed during inspection cannot read them. That scrub also
    drops proxy and CA overrides (HTTPS_PROXY, SSL_CERT_FILE, PIP_INDEX_URL...),
    so behind a corporate TLS-intercepting proxy or private mirror the candidate
    install may fail — an accepted trade-off of the isolation. Shared with the
    behavior probes, which install candidates the same way.
    """

    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    if sys.platform == "win32":
        env["USERPROFILE"] = str(home)
        for key in ("SYSTEMROOT", "SystemRoot", "COMSPEC", "PATHEXT", "TEMP", "TMP"):
            value = os.environ.get(key)
            if value:
                env[key] = value
    return env


def snapshot_candidate_in_venv(
    package: str,
    version: str,
    *,
    python: str = sys.executable,
    timeout: int = 300,
) -> ApiSnapshot:
    """Inspect a candidate version in an isolated temporary virtualenv."""

    module_name = DEFAULT_MODULES.get(package, package.replace("-", "_"))
    with tempfile.TemporaryDirectory(prefix="ark-sdk-snapshot-") as directory:
        venv = Path(directory) / ".venv"
        # Scrub the environment for every subprocess that touches freshly downloaded
        # upstream code: give it a throwaway HOME and only PATH, so a malicious or
        # buggy candidate package cannot read the caller's credentials/config.
        env = isolated_env(Path(directory))
        venv_result = _run_snapshot_subprocess(
            (python, "-m", "venv", str(venv)), env=env, timeout=timeout
        )
        if venv_result is not None:
            return _failed_snapshot(
                package, version, module_name, venv_result, "venv creation failed"
            )
        bin_dir = "Scripts" if sys.platform == "win32" else "bin"
        venv_python = venv / bin_dir / "python"
        install_result = _run_snapshot_subprocess(
            (str(venv_python), "-m", "pip", "install", f"{package}=={version}"),
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        if install_result is not None:
            return _failed_snapshot(
                package, version, module_name, install_result, "pip install failed"
            )
        completed = subprocess.run(
            (
                str(venv_python),
                "-c",
                _SNAPSHOT_SCRIPT,
                package,
                version,
                module_name,
            ),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )
        if completed.returncode != 0:
            return _failed_snapshot(
                package,
                version,
                module_name,
                completed.stderr or completed.stdout,
                "snapshot probe failed",
            )
    raw = json.loads(completed.stdout)
    return ApiSnapshot(
        package=raw["package"],
        version=raw["version"],
        module=raw["module"],
        requested_version=raw.get("requested_version"),
        observed_version=raw.get("observed_version"),
        provenance=raw.get("provenance", "observed"),
        members=tuple(ApiMember(**item) for item in raw.get("members", ())),
        import_error=raw.get("import_error"),
        source="isolated-venv",
    )


def _member_kind(value: Any) -> str:
    if inspect.isclass(value):
        return "class"
    if inspect.isfunction(value) or inspect.ismethod(value):
        return "function"
    if inspect.ismodule(value):
        return "module"
    return type(value).__name__


def _signature(value: Any) -> str:
    try:
        return str(inspect.signature(value))
    except (TypeError, ValueError):
        return ""


def _observed_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def _provenance(requested: str | None, observed: str | None) -> str:
    if requested and observed and requested != observed:
        return "mismatched"
    if observed is None:
        return "not-observed"
    return "observed"


def _tail(text: str, *, limit: int = 500) -> str:
    return text[-limit:]


def _failed_snapshot(
    package: str,
    version: str,
    module_name: str,
    stderr: str,
    reason: str,
) -> ApiSnapshot:
    return ApiSnapshot(
        package=package,
        version=version,
        module=module_name,
        requested_version=version,
        observed_version=None,
        provenance="not-observed",
        import_error=f"{reason}: {_tail(stderr)}",
        source="isolated-venv",
    )


def _run_snapshot_subprocess(
    command: tuple[str, ...],
    *,
    env: dict[str, str],
    timeout: int,
    text: bool = True,
    capture_output: bool = True,
) -> str | None:
    try:
        completed = subprocess.run(
            command,
            text=text,
            capture_output=capture_output,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {timeout}s"
    if completed.returncode == 0:
        return None
    return _tail(completed.stderr or completed.stdout)


_SNAPSHOT_SCRIPT = textwrap.dedent(
    """
    import importlib
    import importlib.metadata
    import inspect
    import json
    import sys

    package, version, module_name = sys.argv[1:4]
    try:
        observed_version = importlib.metadata.version(package)
    except Exception:
        observed_version = None
    provenance = (
        "mismatched" if version and observed_version and version != observed_version
        else "not-observed" if observed_version is None
        else "observed"
    )
    try:
        module = importlib.import_module(module_name)
        members = []
        for name, value in inspect.getmembers(module):
            if name.startswith("_"):
                continue
            try:
                signature = str(inspect.signature(value))
            except (TypeError, ValueError):
                signature = ""
            if inspect.isclass(value):
                kind = "class"
            elif inspect.isfunction(value) or inspect.ismethod(value):
                kind = "function"
            elif inspect.ismodule(value):
                kind = "module"
            else:
                kind = type(value).__name__
            members.append({
                "name": name,
                "kind": kind,
                "signature": signature,
                "module": str(getattr(value, "__module__", "")),
            })
        payload = {
            "package": package,
            "version": version,
            "requested_version": version,
            "observed_version": observed_version,
            "provenance": provenance,
            "module": module_name,
            "members": sorted(members, key=lambda item: item["name"]),
            "import_error": None,
        }
    except Exception as exc:
        payload = {
            "package": package,
            "version": version,
            "requested_version": version,
            "observed_version": observed_version,
            "provenance": provenance,
            "module": module_name,
            "members": [],
            "import_error": str(exc),
        }
    print(json.dumps(payload, sort_keys=True))
    """
).strip()
