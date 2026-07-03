"""Lockfile-diff resolver evidence for SDK evolution runs.

The resolver copies the minimal project metadata that uv needs into a temporary
directory, runs targeted ``uv lock -P`` there, and diffs the before/after
lockfiles. The real workspace is never mutated by candidate detection.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from examples.sdk_evolution_agent.collectors import (
    CommandRunner,
    _call_runner,
    cutoff_free_env,
    read_pyproject_dependency_specs,
    read_uv_lock_versions,
    run_command,
    upload_time_for_version,
)
from examples.sdk_evolution_agent.models import CommandResult


@dataclass(frozen=True)
class UpdateCandidate:
    package: str
    from_version: str
    to_version: str
    blocked_by_cap: str | None = None
    cutoff_delayed_until: str | None = None


def resolve_update_candidates(
    root: Path,
    packages: Sequence[str],
    *,
    command_runner: CommandRunner | None = None,
) -> tuple[tuple[UpdateCandidate, ...], CommandResult]:
    """Resolve adoptable update candidates by diffing a temp lockfile."""

    return _resolve_in_temp(root, packages, command_runner=command_runner)


def resolve_constraint_horizon_candidates(
    root: Path,
    packages: Sequence[str],
    *,
    adoptable: Sequence[UpdateCandidate] = (),
    pypi_metadata: Mapping[str, Mapping[str, object]] | None = None,
    command_runner: CommandRunner | None = None,
) -> tuple[UpdateCandidate, ...]:
    """Resolve candidates hidden by upper caps or the freshness cutoff."""

    pyproject = root / "pyproject.toml"
    if not pyproject.exists():
        return ()
    original_specs = read_pyproject_dependency_specs(pyproject)
    raised_text, raised_caps = raise_upper_bounds_in_pyproject_text(
        pyproject.read_text(encoding="utf-8"), packages
    )
    candidates, result = _resolve_in_temp(
        root,
        packages,
        pyproject_text=raised_text,
        strip_project_cutoff=True,
        command_runner=command_runner,
    )
    if result.returncode != 0:
        return ()

    adoptable_keys = {(candidate.package, candidate.to_version) for candidate in adoptable}
    metadata = pypi_metadata or {}
    horizon: list[UpdateCandidate] = []
    for candidate in candidates:
        if (candidate.package, candidate.to_version) in adoptable_keys:
            continue
        cap = raised_caps.get(candidate.package)
        blocked_by_cap = cap.current if cap else _upper_bound(original_specs.get(candidate.package))
        cutoff_delayed_until = None
        if blocked_by_cap is None:
            upload = upload_time_for_version(
                metadata.get(candidate.package, {}), candidate.to_version
            )
            if upload is not None:
                cutoff_delayed_until = (upload + timedelta(days=8)).date().isoformat()
        horizon.append(
            UpdateCandidate(
                package=candidate.package,
                from_version=candidate.from_version,
                to_version=candidate.to_version,
                blocked_by_cap=blocked_by_cap,
                cutoff_delayed_until=cutoff_delayed_until,
            )
        )
    return tuple(horizon)


@dataclass(frozen=True)
class RaisedCap:
    package: str
    current: str
    replacement: str


def raise_upper_bounds_in_pyproject_text(
    text: str, packages: Sequence[str], *, versions: Mapping[str, str] | None = None
) -> tuple[str, dict[str, RaisedCap]]:
    """Raise only tracked package upper bounds enough to admit candidate versions."""

    result = text
    raised: dict[str, RaisedCap] = {}
    for package in packages:
        pattern = re.compile(
            rf"(?P<prefix>{re.escape(package)}[^\"']*?,)(?P<cap><\s*\d+(?:\.\d+)*)"
        )
        match = pattern.search(result)
        if not match:
            continue
        current = match.group("cap").replace(" ", "")
        replacement = _raised_upper_bound(current, versions.get(package) if versions else None)
        if replacement == current:
            continue
        result = result[: match.start("cap")] + replacement + result[match.end("cap") :]
        raised[package] = RaisedCap(package=package, current=current, replacement=replacement)
    result = _remove_tool_uv_exclude_newer(result)
    return result, raised


def raise_package_cap_in_workspace(root: Path, package: str, version: str) -> RaisedCap | None:
    """Raise one package cap in the real pyproject, preserving floors."""

    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    updated, raised = raise_upper_bounds_in_pyproject_text(
        text, (package,), versions={package: version}
    )
    cap = raised.get(package)
    if cap is None:
        return None
    path.write_text(updated, encoding="utf-8")
    return cap


def candidate_map(
    candidates: Sequence[Mapping[str, object]] | Sequence[UpdateCandidate],
) -> dict[str, str]:
    """Return package -> candidate version for serialized or typed candidates."""

    mapped: dict[str, str] = {}
    for candidate in candidates:
        if isinstance(candidate, UpdateCandidate):
            mapped[candidate.package] = candidate.to_version
        elif isinstance(candidate, Mapping):
            package = candidate.get("package")
            version = candidate.get("to_version")
            if package and version:
                mapped[str(package)] = str(version)
    return mapped


def _resolve_in_temp(
    root: Path,
    packages: Sequence[str],
    *,
    pyproject_text: str | None = None,
    strip_project_cutoff: bool = False,
    command_runner: CommandRunner | None = None,
) -> tuple[tuple[UpdateCandidate, ...], CommandResult]:
    runner = command_runner or run_command
    baseline = read_uv_lock_versions(root / "uv.lock")
    with tempfile.TemporaryDirectory(prefix="ark-sdk-resolver-") as directory:
        temp = Path(directory)
        _copy_minimal_project(root, temp)
        if pyproject_text is not None:
            text = (
                _remove_tool_uv_exclude_newer(pyproject_text)
                if strip_project_cutoff
                else pyproject_text
            )
            (temp / "pyproject.toml").write_text(text, encoding="utf-8")
        env, removed = cutoff_free_env()
        command = ["uv", "lock"]
        for package in packages:
            command.extend(("-P", package))
        result = _call_runner(runner, tuple(command), cwd=temp, env=env, timeout=300)
        result = CommandResult(
            command=result.command,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            removed_env=removed,
        )
        if result.returncode != 0:
            return (), result
        resolved = read_uv_lock_versions(temp / "uv.lock")
    candidates = tuple(
        UpdateCandidate(package=package, from_version=from_version, to_version=to_version)
        for package in packages
        if (from_version := baseline.get(package))
        and (to_version := resolved.get(package))
        and to_version != from_version
    )
    return candidates, result


def _copy_minimal_project(root: Path, temp: Path) -> None:
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        source = root / name
        target = temp / name
        if source.exists():
            shutil.copy2(source, target)
        elif name == "README.md":
            target.write_text("# temporary SDK evolution resolver project\n", encoding="utf-8")


def _raised_upper_bound(current: str, version: str | None) -> str:
    numbers = [int(part) for part in current.removeprefix("<").split(".") if part.isdigit()]
    if version:
        release = [int(part) for part in re.split(r"[^\d]+", version) if part.isdigit()]
        while len(release) < 2:
            release.append(0)
        if len(numbers) >= 2 and release[:2] < numbers[:2]:
            return current
        if len(numbers) >= 2:
            return f"<{release[0]}.{release[1] + 1}"
        return f"<{release[0] + 1}"
    if len(numbers) >= 2:
        return f"<{numbers[0]}.{numbers[1] + 1}"
    if numbers:
        return f"<{numbers[0] + 1}"
    return current


def _upper_bound(spec: str | None) -> str | None:
    if not spec:
        return None
    match = re.search(r"<\s*\d+(?:\.\d+)*", spec)
    return match.group(0).replace(" ", "") if match else None


def _remove_tool_uv_exclude_newer(text: str) -> str:
    return re.sub(r'(?m)^\s*exclude-newer\s*=\s*["\'][^"\']+["\']\s*\n?', "", text)
