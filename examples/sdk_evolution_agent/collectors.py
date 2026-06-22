"""Deterministic upstream SDK research collectors."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shlex
import subprocess
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    CommandResult,
    PackageVersionState,
    SourceRef,
    to_jsonable,
)

FRESHNESS_CUTOFF_ENV_VARS = ("UV_EXCLUDE_NEWER",)

PACKAGE_SOURCE_HINTS: dict[str, tuple[SourceRef, ...]] = {
    "claude-agent-sdk": (
        SourceRef(
            kind="docs",
            label="Claude Agent SDK docs",
            url="https://docs.anthropic.com/en/docs/claude-code/sdk",
        ),
    ),
    "openai-codex": (
        SourceRef(
            kind="docs",
            label="OpenAI Codex SDK docs",
            url="https://developers.openai.com/codex/sdk",
        ),
    ),
    "openai-codex-cli-bin": (
        SourceRef(
            kind="package",
            label="Codex CLI binary runtime package",
            url="https://pypi.org/project/openai-codex-cli-bin/",
        ),
    ),
    "google-antigravity": (
        SourceRef(
            kind="repository",
            label="Google Antigravity SDK repository",
            url="https://github.com/google-antigravity/antigravity-sdk-python",
        ),
    ),
}

CommandRunner = Callable[..., CommandResult]
PypiClient = Callable[[str], Mapping[str, Any]]


def collect_evidence(
    root: Path,
    *,
    packages: Sequence[str] = DEFAULT_PACKAGES,
    include_refresh_preview: bool = False,
    pypi_client: PypiClient | None = None,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    """Collect deterministic package evidence for one run."""

    pypi_client = pypi_client or fetch_pypi_metadata
    command_runner = command_runner or run_command
    pyproject_specs = read_pyproject_dependency_specs(root / "pyproject.toml")
    locked_versions = read_uv_lock_versions(root / "uv.lock")
    package_states = detect_package_versions(
        packages,
        pyproject_specs=pyproject_specs,
        locked_versions=locked_versions,
        pypi_client=pypi_client,
    )
    evidence: dict[str, Any] = {
        "workspace": str(root),
        "packages": [to_jsonable(item) for item in package_states],
        "adapter_sources": [to_jsonable(item) for item in adapter_source_refs(root)],
        "refresh_preview": None,
        "facts": [
            "pyproject.toml dependency declarations are lower bounds or constraints.",
            "uv.lock is the tested local dependency state.",
        ],
    }
    if include_refresh_preview:
        preview = run_refresh_preview(root, packages, command_runner=command_runner)
        evidence["refresh_preview"] = to_jsonable(preview)
    return evidence


def read_pyproject_dependency_specs(path: Path) -> dict[str, str]:
    """Read package constraints from pyproject.toml."""

    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    specs: dict[str, str] = {}
    for package in DEFAULT_PACKAGES:
        match = re.search(rf"{re.escape(package)}[^\"'\],]*", text)
        if match:
            specs[package] = match.group(0)
    return specs


def read_uv_lock_versions(path: Path) -> dict[str, str]:
    """Read package versions from uv.lock without depending on a TOML parser."""

    if not path.exists():
        return {}
    versions: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n\[\[package\]\]\n", text):
        name_match = re.search(r'^name = "([^"]+)"', block, re.MULTILINE)
        version_match = re.search(r'^version = "([^"]+)"', block, re.MULTILINE)
        if name_match and version_match:
            versions[name_match.group(1)] = version_match.group(1)
    return versions


def adapter_source_refs(root: Path) -> tuple[SourceRef, ...]:
    """Return source references for current adapter implementation files."""

    refs: list[SourceRef] = []
    for name in ("claude.py", "codex.py", "antigravity.py", "__init__.py"):
        path = root / "src" / "agent_runtime_kit" / "adapters" / name
        refs.append(
            SourceRef(
                kind="adapter-source",
                label=f"agent-runtime-kit adapter source {name}",
                path=str(path),
                available=path.exists(),
            )
        )
    return tuple(refs)


def detect_package_versions(
    packages: Sequence[str],
    *,
    pyproject_specs: Mapping[str, str],
    locked_versions: Mapping[str, str],
    pypi_client: PypiClient,
) -> tuple[PackageVersionState, ...]:
    """Detect local and upstream version state for vendor SDK packages."""

    states: list[PackageVersionState] = []
    for package in packages:
        sources = list(PACKAGE_SOURCE_HINTS.get(package, ()))
        latest_version: str | None = None
        recent_versions: tuple[str, ...] = ()
        unavailable_reason = ""
        try:
            metadata = pypi_client(package)
            latest_version = str(metadata.get("info", {}).get("version") or "")
            if not latest_version:
                latest_version = None
            recent_versions = select_recent_versions(metadata.get("releases", {}))
            sources.append(
                SourceRef(
                    kind="package-metadata",
                    label=f"PyPI metadata for {package}",
                    url=f"https://pypi.org/project/{package}/",
                    version=latest_version,
                )
            )
        except Exception as exc:
            unavailable_reason = str(exc)
            sources.append(
                SourceRef(
                    kind="package-metadata",
                    label=f"PyPI metadata for {package}",
                    url=f"https://pypi.org/project/{package}/",
                    available=False,
                    note=unavailable_reason,
                )
            )
        states.append(
            PackageVersionState(
                name=package,
                pyproject_spec=pyproject_specs.get(package),
                locked_version=locked_versions.get(package),
                installed_version=installed_version(package),
                latest_version=latest_version,
                recent_versions=recent_versions,
                sources=tuple(sources),
                unavailable_reason=unavailable_reason,
            )
        )
    return tuple(states)


def fetch_pypi_metadata(package: str) -> Mapping[str, Any]:
    """Fetch PyPI JSON metadata for one package."""

    url = f"https://pypi.org/pypi/{package}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "agent-runtime-kit-sdk-evolution"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def installed_version(package: str) -> str | None:
    """Return installed distribution version, if present."""

    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def select_recent_versions(releases: Mapping[str, Any], *, limit: int = 3) -> tuple[str, ...]:
    """Select recent versions using a small dependency-free version key."""

    available = [version for version, files in releases.items() if files]
    return tuple(sorted(available, key=_version_key, reverse=True)[:limit])


def cutoff_free_env(env: Mapping[str, str] | None = None) -> tuple[dict[str, str], tuple[str, ...]]:
    """Return an environment with freshness cutoff variables removed."""

    source = dict(env or os.environ)
    removed = tuple(
        sorted(
            key
            for key in source
            if key in FRESHNESS_CUTOFF_ENV_VARS
            or key.startswith("UV_EXCLUDE_NEWER_")
            or key.endswith("_EXCLUDE_NEWER")
        )
    )
    for key in removed:
        source.pop(key, None)
    return source, removed


def build_refresh_preview_command(packages: Sequence[str]) -> tuple[str, ...]:
    """Build the targeted uv refresh preview command."""

    command = ["uv", "lock", "--dry-run"]
    for package in packages:
        command.extend(("-P", package))
    return tuple(command)


def run_refresh_preview(
    root: Path,
    packages: Sequence[str],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Run a targeted uv lock preview with freshness cutoffs removed."""

    command_runner = command_runner or run_command
    env, removed = cutoff_free_env()
    command = build_refresh_preview_command(packages)
    result = command_runner(command, cwd=root, env=env)
    return CommandResult(
        command=result.command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        removed_env=removed,
    )


def run_verification_commands(
    root: Path,
    commands: Sequence[str],
    *,
    command_runner: CommandRunner | None = None,
) -> tuple[CommandResult, ...]:
    """Run verification commands requested by an architecture decision."""

    command_runner = command_runner or run_command
    results: list[CommandResult] = []
    for command in commands:
        results.append(command_runner(tuple(shlex.split(command)), cwd=root))
    return tuple(results)


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
) -> CommandResult:
    """Run a local command and capture output."""

    completed = subprocess.run(
        tuple(command),
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _version_key(version: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.split(r"[.\-+_]", version):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)
