"""API snapshot and diff helpers for upstream SDK inspection."""

from __future__ import annotations

import importlib
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
    "openai-codex-cli-bin": "codex_cli_bin",
    "google-antigravity": "google.antigravity",
}


def snapshot_current_api(package: str, *, version: str | None = None) -> ApiSnapshot:
    """Capture public API for a package importable in the current environment."""

    module_name = DEFAULT_MODULES.get(package, package.replace("-", "_"))
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        return ApiSnapshot(
            package=package,
            version=version,
            module=module_name,
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
        version=version or str(getattr(module, "__version__", "") or ""),
        module=module_name,
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
    step = "virtual environment creation"
    try:
        with tempfile.TemporaryDirectory(prefix="ark-sdk-snapshot-") as directory:
            venv = Path(directory) / ".venv"
            # Scrub the environment for every subprocess that touches freshly downloaded
            # upstream code: give it a throwaway HOME and only PATH, so a malicious or
            # buggy candidate package cannot read the caller's credentials/config.
            env = isolated_env(Path(directory))
            subprocess.run(
                (python, "-m", "venv", str(venv)),
                check=True,
                timeout=timeout,
                env=env,
            )
            bin_dir = "Scripts" if sys.platform == "win32" else "bin"
            venv_python = venv / bin_dir / "python"
            step = "package installation"
            subprocess.run(
                (str(venv_python), "-m", "pip", "install", f"{package}=={version}"),
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
            step = "snapshot execution"
            completed = subprocess.run(
                (
                    str(venv_python),
                    "-c",
                    _SNAPSHOT_SCRIPT,
                    package,
                    version,
                    module_name,
                ),
                check=True,
                text=True,
                capture_output=True,
                timeout=timeout,
                env=env,
            )
    except subprocess.TimeoutExpired as exc:
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} timed out after {exc.timeout}s",
        )
    except subprocess.CalledProcessError as exc:
        detail = _bounded_failure_detail(exc.stderr or exc.stdout or str(exc))
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} failed: {detail}",
        )
    except OSError as exc:
        return _failed_isolated_snapshot(
            package,
            version,
            module_name,
            f"{step} failed: {_bounded_failure_detail(exc)}",
        )

    try:
        raw = json.loads(completed.stdout)
        return ApiSnapshot(
            package=raw["package"],
            version=raw["version"],
            module=raw["module"],
            members=tuple(ApiMember(**item) for item in raw.get("members", ())),
            import_error=raw.get("import_error"),
            source="isolated-venv",
        )
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        output = _bounded_failure_detail(completed.stdout)
        detail = f"malformed snapshot output: {exc}"
        if output:
            detail += f"; stdout={output}"
        return _failed_isolated_snapshot(package, version, module_name, detail)


def _failed_isolated_snapshot(
    package: str,
    version: str,
    module_name: str,
    error: str,
) -> ApiSnapshot:
    return ApiSnapshot(
        package=package,
        version=version,
        module=module_name,
        import_error=_bounded_failure_detail(error, limit=560),
        source="isolated-venv",
    )


def _bounded_failure_detail(value: object, *, limit: int = 480) -> str:
    if isinstance(value, bytes):
        text = value.decode(errors="replace")
    else:
        text = str(value)
    return " ".join(text.split())[:limit]


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


_SNAPSHOT_SCRIPT = textwrap.dedent(
    """
    import importlib
    import inspect
    import json
    import sys

    package, version, module_name = sys.argv[1:4]
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
            "module": module_name,
            "members": sorted(members, key=lambda item: item["name"]),
            "import_error": None,
        }
    except Exception as exc:
        payload = {
            "package": package,
            "version": version,
            "module": module_name,
            "members": [],
            "import_error": str(exc),
        }
    print(json.dumps(payload, sort_keys=True))
    """
).strip()
