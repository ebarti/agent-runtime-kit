"""Deterministic upstream SDK research collectors."""

from __future__ import annotations

import importlib.metadata
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, Specifier
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

from examples.sdk_evolution_agent.models import (
    DEFAULT_PACKAGES,
    CommandResult,
    PackageVersionState,
    SourceRef,
    to_jsonable,
)

FRESHNESS_CUTOFF_ENV_VARS = ("UV_EXCLUDE_NEWER",)
SDK_EVOLUTION_EXCLUDE_NEWER = "false"

_REFRESH_TRANSITION_RE = re.compile(
    r"^[ \t]*Update[ \t]+(?P<package>[A-Za-z0-9_.-]+)[ \t]+"
    r"v(?P<from_version>\S+)[ \t]+->[ \t]+v(?P<to_version>\S+)[ \t]*$",
    re.MULTILINE,
)


@dataclass(frozen=True, order=True)
class ResolverTransition:
    """One exact package transition selected by the uv refresh preview."""

    package: str
    from_version: str
    to_version: str


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
        current_preview = run_refresh_preview(root, packages, command_runner=command_runner)
        evidence["refresh_preview"] = to_jsonable(current_preview)
        evidence["packages"] = _annotate_candidate_states(evidence)
        if _needs_prospective_preview(evidence):
            prospective = run_prospective_refresh_preview(
                root,
                packages,
                candidate_update_versions(evidence),
                command_runner=command_runner,
            )
            evidence["current_refresh_preview"] = evidence["refresh_preview"]
            evidence["refresh_preview"] = to_jsonable(prospective)
    evidence["packages"] = _annotate_candidate_states(evidence)
    return evidence


def read_pyproject_dependency_specs(path: Path) -> dict[str, str]:
    """Read package constraints from pyproject.toml."""

    if not path.exists():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    monitored = {canonicalize_name(package): package for package in DEFAULT_PACKAGES}
    specs: dict[str, str] = {}
    for raw in _project_requirement_strings(data, include_constraints=True):
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            continue
        package = monitored.get(canonicalize_name(requirement.name))
        if package is not None:
            specs.setdefault(package, raw)
    return specs


def _project_requirement_strings(
    data: Mapping[str, Any],
    *,
    include_constraints: bool,
) -> tuple[str, ...]:
    """Return actual project requirement strings, never comments or keyword mentions."""

    requirements: list[str] = []
    project = data.get("project")
    if isinstance(project, Mapping):
        direct = project.get("dependencies")
        if isinstance(direct, list):
            requirements.extend(item for item in direct if isinstance(item, str))
        optional = project.get("optional-dependencies")
        if isinstance(optional, Mapping):
            for group in optional.values():
                if isinstance(group, list):
                    requirements.extend(item for item in group if isinstance(item, str))
    if include_constraints:
        tool = data.get("tool")
        uv = tool.get("uv") if isinstance(tool, Mapping) else None
        constraints = uv.get("constraint-dependencies") if isinstance(uv, Mapping) else None
        if isinstance(constraints, list):
            requirements.extend(item for item in constraints if isinstance(item, str))
    return tuple(requirements)


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
    metadata_by_package: dict[str, Mapping[str, Any]] = {}
    for package in packages:
        sources = list(PACKAGE_SOURCE_HINTS.get(package, ()))
        latest_version: str | None = None
        recent_versions: tuple[str, ...] = ()
        unavailable_reason = ""
        try:
            metadata = pypi_client(package)
            metadata_by_package[package] = metadata
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
        installed = installed_version(package)
        baseline = locked_versions.get(package) or installed
        candidate = latest_version if _is_newer_version(latest_version, baseline) else None
        states.append(
            PackageVersionState(
                name=package,
                pyproject_spec=pyproject_specs.get(package),
                locked_version=locked_versions.get(package),
                installed_version=installed,
                latest_version=latest_version,
                candidate_version=candidate,
                candidate_status=(
                    "upstream-newer-unresolved"
                    if candidate is not None
                    else "metadata-unavailable"
                    if latest_version is None
                    else "current"
                ),
                recent_versions=recent_versions,
                sources=tuple(sources),
                unavailable_reason=unavailable_reason,
            )
        )
    sdk_selected_version, sdk_requirement = _exact_runtime_dependency(
        metadata_by_package.get("openai-codex"),
        dependency="openai-codex-cli-bin",
    )
    if sdk_selected_version is not None:
        states = [
            replace(
                state,
                sdk_selected_version=sdk_selected_version,
                sdk_requirement=sdk_requirement,
            )
            if state.name == "openai-codex-cli-bin"
            else state
            for state in states
        ]
    return tuple(states)


def _exact_runtime_dependency(
    metadata: Mapping[str, Any] | None,
    *,
    dependency: str,
) -> tuple[str | None, str | None]:
    """Return an exact dependency pin advertised by the latest SDK metadata."""

    if not isinstance(metadata, Mapping):
        return None, None
    info = metadata.get("info")
    requires_dist = info.get("requires_dist") if isinstance(info, Mapping) else None
    if not isinstance(requires_dist, Sequence) or isinstance(requires_dist, str | bytes):
        return None, None
    for raw in requires_dist:
        if not isinstance(raw, str):
            continue
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            continue
        if canonicalize_name(requirement.name) != canonicalize_name(dependency):
            continue
        for specifier in requirement.specifier:
            if specifier.operator == "==" and "*" not in specifier.version:
                return specifier.version, raw
    return None, None


def fetch_pypi_metadata(package: str) -> Mapping[str, Any]:
    """Fetch PyPI JSON metadata for one package."""

    url = f"https://pypi.org/pypi/{package}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "agent-runtime-kit-sdk-evolution"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise AssertionError("unreachable")


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

    command = ["uv", "lock", "--dry-run", "--exclude-newer", SDK_EVOLUTION_EXCLUDE_NEWER]
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


def run_prospective_refresh_preview(
    root: Path,
    packages: Sequence[str],
    candidates: Mapping[str, str],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Preview candidates beyond current upper bounds without mutating the checkout."""

    command_runner = command_runner or run_command
    with tempfile.TemporaryDirectory(prefix="ark-sdk-prospective-") as directory:
        workspace = Path(directory)
        for name in ("pyproject.toml", "uv.lock"):
            source = root / name
            if source.exists():
                shutil.copy2(source, workspace / name)
        pyproject = workspace / "pyproject.toml"
        if not pyproject.exists():
            return CommandResult(
                command=build_refresh_preview_command(packages),
                returncode=2,
                stderr="prospective preview requires pyproject.toml",
            )
        widen_project_dependency_bounds(pyproject, candidates)
        return run_refresh_preview(workspace, packages, command_runner=command_runner)


def parse_refresh_transitions(evidence: Mapping[str, Any]) -> tuple[ResolverTransition, ...]:
    """Parse exact resolver transitions from refresh-preview stdout and stderr."""

    preview = evidence.get("refresh_preview")
    if not isinstance(preview, Mapping):
        return ()
    text = f"{preview.get('stdout') or ''}\n{preview.get('stderr') or ''}"
    transitions = {
        ResolverTransition(
            package=match.group("package"),
            from_version=match.group("from_version"),
            to_version=match.group("to_version"),
        )
        for match in _REFRESH_TRANSITION_RE.finditer(text)
    }
    return tuple(sorted(transitions))


def refresh_update_versions(evidence: Mapping[str, Any]) -> dict[str, str]:
    """Return resolver-selected target versions keyed by exact package name."""

    return {
        transition.package: transition.to_version
        for transition in parse_refresh_transitions(evidence)
    }


def candidate_update_versions(evidence: Mapping[str, Any]) -> dict[str, str]:
    """Return every newer upstream candidate, independent of current resolver caps."""

    resolver_targets = refresh_update_versions(evidence)
    updates: dict[str, str] = {}
    packages = evidence.get("packages")
    if not isinstance(packages, Sequence) or isinstance(packages, str | bytes):
        return updates
    for package in packages:
        if not isinstance(package, Mapping):
            continue
        name = str(package.get("name") or "")
        if not name:
            continue
        baseline = _string_or_none(package.get("locked_version")) or _string_or_none(
            package.get("installed_version")
        )
        candidate_key_present = "candidate_version" in package
        candidate = _string_or_none(package.get("candidate_version"))
        latest = _string_or_none(package.get("latest_version"))
        if candidate is None and not candidate_key_present and _is_newer_version(latest, baseline):
            candidate = latest
        if candidate is None:
            candidate = resolver_targets.get(name)
        if candidate is not None and candidate != baseline:
            updates[name] = candidate
    return updates


def candidate_transitions(evidence: Mapping[str, Any]) -> tuple[ResolverTransition, ...]:
    """Build the complete baseline-to-upstream candidate set for safety gates."""

    updates = candidate_update_versions(evidence)
    transitions: list[ResolverTransition] = []
    packages = evidence.get("packages")
    if isinstance(packages, Sequence) and not isinstance(packages, str | bytes):
        for package in packages:
            if not isinstance(package, Mapping):
                continue
            name = str(package.get("name") or "")
            candidate = updates.get(name)
            baseline = _string_or_none(package.get("locked_version")) or _string_or_none(
                package.get("installed_version")
            )
            if name and candidate and baseline:
                transitions.append(ResolverTransition(name, baseline, candidate))
    # Resolver transitions are supplemental evidence for older/minimal bundles
    # that lack package metadata. They never replace independently discovered
    # candidates when the package inventory is present.
    covered = {transition.package for transition in transitions}
    transitions.extend(
        transition
        for transition in parse_refresh_transitions(evidence)
        if transition.package not in covered
    )
    return tuple(sorted(set(transitions)))


def _annotate_candidate_states(evidence: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Classify upstream freshness separately from current resolver selection."""

    resolver_targets = refresh_update_versions(evidence)
    current_preview = evidence.get("current_refresh_preview")
    current_targets = (
        refresh_update_versions({"refresh_preview": current_preview})
        if isinstance(current_preview, Mapping)
        else resolver_targets
    )
    raw_packages = evidence.get("packages")
    if not isinstance(raw_packages, Sequence) or isinstance(raw_packages, str | bytes):
        return []
    packages: list[dict[str, Any]] = []
    for raw in raw_packages:
        if not isinstance(raw, Mapping):
            continue
        package = dict(raw)
        name = str(package.get("name") or "")
        baseline = _string_or_none(package.get("locked_version")) or _string_or_none(
            package.get("installed_version")
        )
        latest = _string_or_none(package.get("latest_version"))
        resolver_target = resolver_targets.get(name)
        current_target = current_targets.get(name)
        sdk_selected_version = _string_or_none(package.get("sdk_selected_version"))
        sdk_requirement = _string_or_none(package.get("sdk_requirement"))
        candidate = (
            (resolver_target or sdk_selected_version)
            if name == "openai-codex-cli-bin"
            and (resolver_target or sdk_selected_version) is not None
            and (resolver_target or sdk_selected_version) != baseline
            else latest
            if name != "openai-codex-cli-bin" and _is_newer_version(latest, baseline)
            else None
        )
        status, reason = _candidate_status(
            name=name,
            baseline=baseline,
            candidate=candidate,
            latest=latest,
            resolver_target=resolver_target,
            current_target=current_target,
            pyproject_spec=_string_or_none(package.get("pyproject_spec")),
            metadata_available=latest is not None,
            sdk_selected_version=sdk_selected_version,
            sdk_requirement=sdk_requirement,
        )
        package.update(
            candidate_version=candidate,
            resolver_target_version=resolver_target,
            candidate_status=status,
            candidate_reason=reason,
        )
        packages.append(package)
    return packages


def _candidate_status(
    *,
    name: str,
    baseline: str | None,
    candidate: str | None,
    latest: str | None,
    resolver_target: str | None,
    current_target: str | None,
    pyproject_spec: str | None,
    metadata_available: bool,
    sdk_selected_version: str | None,
    sdk_requirement: str | None,
) -> tuple[str, str]:
    if candidate is None:
        if not metadata_available:
            return "metadata-unavailable", "upstream package metadata was unavailable"
        if name == "openai-codex-cli-bin" and _is_newer_version(latest, baseline):
            selected = sdk_selected_version or baseline or "an earlier runtime"
            requirement = f" ({sdk_requirement})" if sdk_requirement else ""
            return (
                "sdk-coupled-no-update",
                f"latest published openai-codex selects CLI {selected}{requirement}; "
                f"standalone CLI {latest} is a staged runtime artifact, not an "
                "SDK-usable update",
            )
        return "current", "no newer upstream release was observed"
    if current_target == candidate:
        if name == "openai-codex-cli-bin" and latest and latest != candidate:
            return (
                "sdk-coupled-candidate",
                f"current resolution selects the CLI {candidate} required by openai-codex; "
                f"standalone CLI latest is staged at {latest}",
            )
        return "resolver-selected", "current project constraints select the upstream candidate"
    if resolver_target == candidate:
        if name == "openai-codex-cli-bin" and latest and latest != candidate:
            return (
                "prospective-coupled-candidate",
                f"Codex SDK candidate selects CLI {candidate}; standalone CLI latest is {latest}",
            )
        if pyproject_spec and not _requirement_allows(pyproject_spec, candidate):
            return (
                "blocked-by-project-constraint",
                f"{pyproject_spec} excludes candidate {candidate}; "
                "prospective resolution admits it",
            )
        return (
            "prospective-resolver-selected",
            "prospective resolution selected the candidate after relaxing excluding upper bounds",
        )
    if resolver_target:
        return (
            "resolver-selected-nonlatest",
            f"resolver selected {resolver_target}, while upstream latest is {candidate}",
        )
    if name == "openai-codex-cli-bin" and sdk_selected_version == candidate:
        latest_note = f"; standalone CLI latest is {latest}" if latest != candidate else ""
        return (
            "sdk-coupled-candidate",
            f"latest published openai-codex selects CLI {candidate}{latest_note}",
        )
    if pyproject_spec and not _requirement_allows(pyproject_spec, candidate):
        return (
            "blocked-by-project-constraint",
            f"{pyproject_spec} excludes upstream candidate {candidate}",
        )
    if name == "openai-codex-cli-bin":
        return (
            "sdk-coupling-unresolved",
            "the published Codex SDK dependency and resolver did not select the same CLI version",
        )
    return (
        "not-selected-by-resolver",
        f"upstream candidate {candidate} is newer than baseline {baseline or '<missing>'}",
    )


def _needs_prospective_preview(evidence: Mapping[str, Any]) -> bool:
    packages = evidence.get("packages")
    if not isinstance(packages, Sequence) or isinstance(packages, str | bytes):
        return False
    return any(
        isinstance(package, Mapping)
        and package.get("candidate_version")
        and package.get("candidate_status") != "resolver-selected"
        for package in packages
    )


def widen_project_dependency_bounds(
    path: Path,
    candidates: Mapping[str, str],
) -> tuple[str, ...]:
    """Relax excluding upper bounds just enough to admit inspected candidates."""

    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    monitored = {canonicalize_name(name): version for name, version in candidates.items()}
    changes: list[str] = []
    replacements: dict[str, str] = {}
    for raw in _project_requirement_strings(data, include_constraints=False):
        try:
            requirement = Requirement(raw)
        except InvalidRequirement:
            continue
        candidate = monitored.get(canonicalize_name(requirement.name))
        if candidate is None or requirement.specifier.contains(candidate, prereleases=True):
            continue
        widened = _widen_requirement_upper_bound(raw, candidate)
        replacements[raw] = widened
        change = f"{raw} -> {widened}"
        if change not in changes:
            changes.append(change)
    for old, new in replacements.items():
        before = text
        text = text.replace(f'"{old}"', f'"{new}"')
        text = text.replace(f"'{old}'", f"'{new}'")
        if text == before:
            raise ValueError(f"could not locate parsed requirement {old!r} in {path}")
    tomllib.loads(text)
    if changes:
        path.write_text(text, encoding="utf-8")
    return tuple(changes)


def update_compatibility_manifest(
    path: Path,
    pyproject_path: Path,
    candidates: Mapping[str, str],
) -> tuple[str, ...]:
    """Keep committed tested-version metadata aligned with an SDK lock refresh."""

    if not path.exists():
        raise ValueError(f"compatibility manifest is missing: {path}")
    text = path.read_text(encoding="utf-8")
    original = text
    project_specs = read_pyproject_dependency_specs(pyproject_path)
    changes: list[str] = []

    for package, candidate in sorted(candidates.items()):
        entry_span = _compatibility_entry_span(text, package)
        if entry_span is None:
            text, count = re.subn(
                rf'(PackageVersion\(package="{re.escape(package)}", version=")'
                r'[^\"]+("\))',
                rf"\g<1>{candidate}\g<2>",
                text,
            )
            if count != 1:
                raise ValueError(
                    f"expected one compatibility runtime dependency for {package}, found {count}"
                )
            changes.append(f"{package} tested runtime dependency -> {candidate}")
            continue

        start, end = entry_span
        block = text[start:end]
        block, tested_count = re.subn(
            r'(?m)^(        tested_version=")[^\"]+(",)$',
            rf"\g<1>{candidate}\g<2>",
            block,
        )
        if tested_count != 1:
            raise ValueError(
                f"expected one tested_version field for {package}, found {tested_count}"
            )

        raw_requirement = project_specs.get(package)
        if raw_requirement is None:
            raise ValueError(f"project requirement is missing for {package}")
        specifier = _requirement_specifier_text(raw_requirement, package)
        block, specifier_count = re.subn(
            r'(?m)^(        version_specifier=")[^\"]+(",)$',
            rf"\g<1>{specifier}\g<2>",
            block,
        )
        if specifier_count != 1:
            raise ValueError(
                f"expected one version_specifier field for {package}, found {specifier_count}"
            )
        text = text[:start] + block + text[end:]
        changes.append(f"{package} tested version -> {candidate} ({specifier})")

    compile(text, str(path), "exec")
    if text != original:
        path.write_text(text, encoding="utf-8")
    return tuple(changes)


def _compatibility_entry_span(text: str, package: str) -> tuple[int, int] | None:
    package_line = re.compile(rf'(?m)^        package="{re.escape(package)}",$')
    matches = tuple(package_line.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError(f"expected one compatibility entry for {package}, found {len(matches)}")
    start = text.rfind("    RuntimeCompatibility(\n", 0, matches[0].start())
    if start < 0:
        raise ValueError(f"could not find compatibility entry start for {package}")
    next_entry = text.find("    RuntimeCompatibility(\n", matches[0].end())
    end = next_entry if next_entry >= 0 else text.find("\n)\n", matches[0].end())
    if end < 0:
        raise ValueError(f"could not find compatibility entry end for {package}")
    return start, end


def _requirement_specifier_text(raw: str, package: str) -> str:
    requirement = Requirement(raw)
    if canonicalize_name(requirement.name) != canonicalize_name(package):
        raise ValueError(f"requirement {raw!r} does not describe {package}")
    match = re.match(rf"^\s*{re.escape(requirement.name)}\s*", raw, re.IGNORECASE)
    if match is None:
        raise ValueError(f"could not parse requirement spelling from {raw!r}")
    specifier = raw[match.end() :].split(";", 1)[0].strip()
    if not specifier or str(requirement.specifier) == "":
        raise ValueError(f"requirement {raw!r} has no version specifier")
    return specifier


def _widen_requirement_upper_bound(raw: str, candidate: str) -> str:
    next_minor = _next_minor_upper_bound(candidate)
    changed = False

    def replace_upper(match: re.Match[str]) -> str:
        nonlocal changed
        token = match.group(0)
        try:
            allows = Specifier(token).contains(candidate, prereleases=True)
        except InvalidSpecifier:
            return token
        if allows:
            return token
        changed = True
        return f"<{next_minor}"

    widened = re.sub(r"(?<![<>=!~])(?:<=|<)\s*[^,;\s]+", replace_upper, raw)
    if not changed:
        raise ValueError(
            f"{raw!r} excludes {candidate}, but no safely widenable upper bound was found"
        )
    if not Requirement(widened).specifier.contains(candidate, prereleases=True):
        raise ValueError(f"widened requirement {widened!r} still excludes {candidate}")
    return widened


def _next_minor_upper_bound(version: str) -> str:
    parsed = Version(version)
    release = parsed.release
    if len(release) < 2:
        return str(release[0] + 1)
    return f"{release[0]}.{release[1] + 1}"


def _requirement_allows(raw: str, version: str) -> bool:
    try:
        return Requirement(raw).specifier.contains(version, prereleases=True)
    except (InvalidRequirement, InvalidVersion):
        return False


def _is_newer_version(candidate: str | None, baseline: str | None) -> bool:
    if candidate is None:
        return False
    if baseline is None:
        return True
    try:
        return Version(candidate) > Version(baseline)
    except InvalidVersion:
        return _version_key(candidate) > _version_key(baseline)


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def run_lock_update(
    root: Path,
    packages: Sequence[str],
    *,
    command_runner: CommandRunner | None = None,
) -> CommandResult:
    """Apply a targeted uv lock update with freshness cutoffs removed."""

    command_runner = command_runner or run_command
    env, removed = cutoff_free_env()
    command = ["uv", "lock", "--exclude-newer", SDK_EVOLUTION_EXCLUDE_NEWER]
    for package in packages:
        command.extend(("-P", package))
    result = command_runner(tuple(command), cwd=root, env=env)
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
    env, _removed = cutoff_free_env()
    results: list[CommandResult] = []
    for command in commands:
        results.append(command_runner(tuple(shlex.split(command)), cwd=root, env=env))
    return tuple(results)


def run_command(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 900,
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
            stdout=_decode_timeout_output(exc.stdout),
            stderr=(
                f"command timed out after {timeout}s: {_decode_timeout_output(exc.stderr)}"
            ).strip(),
        )
    return CommandResult(
        command=tuple(command),
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _version_key(version: str) -> tuple[Any, ...]:
    parts: list[Any] = []
    for part in re.split(r"[.\-+_]", version):
        parts.append((0, int(part)) if part.isdigit() else (1, part))
    return tuple(parts)
