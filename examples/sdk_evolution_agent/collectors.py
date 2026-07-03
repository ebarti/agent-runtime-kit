"""Deterministic upstream SDK research collectors."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import re
import shlex
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    CommandResult,
    PackageVersionState,
    SourceRef,
    to_jsonable,
)

tomllib: ModuleType | None
if sys.version_info >= (3, 11):  # pragma: no cover - exercised on modern runtimes
    tomllib = importlib.import_module("tomllib")
else:  # pragma: no cover - Python 3.10 fallback
    tomllib = None

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
        "update_candidates": [],
        "update_candidates_beyond_cap": [],
        "facts": [
            "pyproject.toml dependency declarations are lower bounds or constraints.",
            "uv.lock is the tested local dependency state.",
        ],
    }
    if include_refresh_preview:
        from examples.sdk_evolution_agent.resolver import (
            resolve_constraint_horizon_candidates,
            resolve_update_candidates,
        )

        candidates, resolver_result = resolve_update_candidates(
            root, packages, command_runner=command_runner
        )
        preview = run_refresh_preview(root, packages, command_runner=command_runner)
        evidence["refresh_preview"] = to_jsonable(preview)
        evidence["resolver_result"] = to_jsonable(resolver_result)
        evidence["update_candidates"] = [to_jsonable(candidate) for candidate in candidates]
        evidence["update_candidates_beyond_cap"] = [
            to_jsonable(candidate)
            for candidate in resolve_constraint_horizon_candidates(
                root,
                packages,
                adoptable=candidates,
                pypi_metadata={package: _safe_pypi(pypi_client, package) for package in packages},
                command_runner=command_runner,
            )
        ]
    return evidence


def read_pyproject_dependency_specs(path: Path) -> dict[str, str]:
    """Read package constraints from pyproject.toml."""

    if not path.exists():
        return {}
    data = _load_toml(path)
    specs: dict[str, str] = {}
    if data is not None:
        optional = data.get("project", {}).get("optional-dependencies", {})
        if isinstance(optional, dict):
            for dependencies in optional.values():
                if not isinstance(dependencies, list):
                    continue
                for dependency in dependencies:
                    if not isinstance(dependency, str):
                        continue
                    package = _dependency_name(dependency)
                    if package in DEFAULT_PACKAGES:
                        specs[package] = dependency
            return specs

    text = path.read_text(encoding="utf-8")
    specs.update(_read_optional_dependency_specs_from_text(text))
    if specs:
        return specs
    for package in DEFAULT_PACKAGES:
        match = re.search(rf"{re.escape(package)}[^\"'\],]*", text)
        if match:
            specs[package] = match.group(0)
    return specs


def read_uv_lock_versions(path: Path) -> dict[str, str]:
    """Read package versions from uv.lock."""

    if not path.exists():
        return {}
    data = _load_toml(path)
    if data is not None:
        packages = data.get("package", [])
        if isinstance(packages, list):
            toml_versions: dict[str, str] = {}
            for package in packages:
                if not isinstance(package, dict):
                    continue
                name = package.get("name")
                version = package.get("version")
                if isinstance(name, str) and isinstance(version, str):
                    toml_versions[name] = version
            return toml_versions

    regex_versions: dict[str, str] = {}
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n\[\[package\]\]\n", text):
        name_match = re.search(r'^name = "([^"]+)"', block, re.MULTILINE)
        version_match = re.search(r'^version = "([^"]+)"', block, re.MULTILINE)
        if name_match and version_match:
            regex_versions[name_match.group(1)] = version_match.group(1)
    return regex_versions


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
        payload = json.loads(response.read().decode("utf-8"))
    return payload if isinstance(payload, Mapping) else {}


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
    result = _call_runner(command_runner, command, cwd=root, env=env, timeout=300)
    return CommandResult(
        command=result.command,
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        removed_env=removed,
    )


def run_lock_update(
    root: Path,
    packages: Sequence[str],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Apply a targeted uv lock update with freshness cutoffs removed."""

    command_runner = command_runner or run_command
    env, removed = cutoff_free_env()
    command = ["uv", "lock"]
    for package in packages:
        command.extend(("-P", package))
    result = _call_runner(command_runner, tuple(command), cwd=root, env=env, timeout=300)
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
        results.append(
            _call_runner(command_runner, tuple(shlex.split(command)), cwd=root, timeout=900)
        )
    return tuple(results)


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
) -> CommandResult:
    """Run a local command and capture output."""

    try:
        completed = subprocess.run(
            tuple(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CommandResult(
            command=tuple(command),
            returncode=124,
            stdout=str(exc.stdout or ""),
            stderr=f"timed out after {timeout}s",
        )
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _call_runner(
    command_runner: CommandRunner,
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int | None = None,
) -> CommandResult:
    kwargs: dict[str, Any] = {"cwd": cwd}
    if env is not None:
        kwargs["env"] = env
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return command_runner(tuple(command), **kwargs)
    except TypeError as exc:
        if "timeout" not in str(exc):
            raise
        kwargs.pop("timeout", None)
        return command_runner(tuple(command), **kwargs)


def _version_key(version: str) -> tuple[Any, ...]:
    """Return a small dependency-free PEP 440-ish ordering key."""

    text = version.strip().lower().replace("_", ".").replace("-", ".")
    public = text.split("+", 1)[0]
    epoch = 0
    if "!" in public:
        raw_epoch, public = public.split("!", 1)
        epoch = int(raw_epoch or "0") if raw_epoch.isdigit() else 0

    dev_number = _suffix_number(public, "dev")
    post_number = _suffix_number(public, "post")
    pre_match = re.search(r"(a|b|rc)(\d*)", public)
    release_text = re.split(r"(?:a|b|rc|post|dev)", public, maxsplit=1)[0].strip(".")
    release = tuple(int(part) for part in release_text.split(".") if part.isdigit())
    release = release + (0,) * (6 - len(release))

    if dev_number is not None and pre_match is None:
        phase = (0, dev_number)
    elif pre_match is not None:
        phase_order = {"a": 1, "b": 2, "rc": 3}
        phase = (phase_order[pre_match.group(1)], int(pre_match.group(2) or "0"))
    elif post_number is not None:
        phase = (5, post_number)
    else:
        phase = (4, 0)
    return (epoch, release, phase)


def _suffix_number(version: str, label: str) -> int | None:
    match = re.search(rf"(?:^|[.]){label}(\d*)", version)
    if not match:
        return None
    return int(match.group(1) or "0")


def _load_toml(path: Path) -> dict[str, Any] | None:
    if tomllib is None:
        return None
    try:
        return dict(tomllib.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def _dependency_name(spec: str) -> str:
    match = re.match(r"\s*([A-Za-z0-9_.-]+)", spec)
    return (match.group(1) if match else "").replace("_", "-").lower()


def _read_optional_dependency_specs_from_text(text: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    in_optional = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_optional = stripped == "[project.optional-dependencies]"
            continue
        if not in_optional:
            continue
        for dependency in re.findall(r'["\']([^"\']+)["\']', stripped):
            package = _dependency_name(dependency)
            if package in DEFAULT_PACKAGES:
                specs[package] = dependency
    return specs


def _safe_pypi(pypi_client: PypiClient, package: str) -> Mapping[str, Any]:
    try:
        return pypi_client(package)
    except Exception:
        return {}


def upload_time_for_version(metadata: Mapping[str, Any], version: str) -> datetime | None:
    """Return the first PyPI upload timestamp for a version, when present."""

    releases = metadata.get("releases", {})
    if not isinstance(releases, Mapping):
        return None
    files = releases.get(version)
    if not isinstance(files, list) or not files:
        return None
    for file_info in files:
        if not isinstance(file_info, Mapping):
            continue
        raw = file_info.get("upload_time_iso_8601") or file_info.get("upload_time")
        if not isinstance(raw, str) or not raw:
            continue
        normalized = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None
