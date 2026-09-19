"""Structured data used by the SDK evolution agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_PACKAGES = (
    "claude-agent-sdk",
    "openai-codex",
    "openai-codex-cli-bin",
    "google-antigravity",
)

RUNTIME_CONTRACT_SYMBOLS = (
    "AgentTask",
    "AgentResult",
    "RuntimeRegistry",
    "register_adapters",
    "output_schema",
    "event_sink",
    "PermissionProfile",
    "UnsupportedTaskInputError",
)


@dataclass(frozen=True)
class CommandResult:
    """Captured local command result."""

    command: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    removed_env: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRef:
    """Reference to deterministic evidence."""

    kind: str
    label: str
    url: str | None = None
    path: str | None = None
    version: str | None = None
    available: bool = True
    note: str = ""


@dataclass(frozen=True)
class PackageVersionState:
    """Version state for one upstream SDK package."""

    name: str
    pyproject_spec: str | None = None
    locked_version: str | None = None
    installed_version: str | None = None
    latest_version: str | None = None
    candidate_version: str | None = None
    resolver_target_version: str | None = None
    candidate_status: str = "unknown"
    candidate_reason: str = ""
    sdk_selected_version: str | None = None
    sdk_requirement: str | None = None
    recent_versions: tuple[str, ...] = ()
    sources: tuple[SourceRef, ...] = ()
    unavailable_reason: str = ""


@dataclass(frozen=True)
class ApiMember:
    """One public API member in a package snapshot."""

    name: str
    kind: str
    signature: str = ""
    module: str = ""


@dataclass(frozen=True)
class ImplementationFile:
    """Content fingerprint for one file shipped inside an SDK implementation."""

    path: str
    kind: str
    sha256: str
    size: int
    line_count: int = 0


@dataclass(frozen=True)
class ImplementationDefinition:
    """AST fingerprint for one Python definition in an SDK implementation."""

    name: str
    kind: str
    path: str
    sha256: str


@dataclass(frozen=True)
class ApiSnapshot:
    """Public API and implementation snapshot for an inspected package version."""

    package: str
    version: str | None
    module: str
    members: tuple[ApiMember, ...] = ()
    implementation_files: tuple[ImplementationFile, ...] = ()
    implementation_definitions: tuple[ImplementationDefinition, ...] = ()
    implementation_status: str = "unavailable"
    implementation_note: str = ""
    import_error: str | None = None
    source: str = "current-environment"


@dataclass(frozen=True)
class ApiDiff:
    """Diff between two API snapshots."""

    package: str
    from_version: str | None
    to_version: str | None
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    changed: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImplementationDiff:
    """Observed implementation changes between two exact SDK releases."""

    package: str
    from_version: str | None
    to_version: str | None
    status: str
    python_files_before: int = 0
    python_files_after: int = 0
    source_lines_before: int = 0
    source_lines_after: int = 0
    files_added: tuple[str, ...] = ()
    files_removed: tuple[str, ...] = ()
    files_changed: tuple[str, ...] = ()
    definitions_added: tuple[str, ...] = ()
    definitions_removed: tuple[str, ...] = ()
    definitions_changed: tuple[str, ...] = ()
    opaque_artifacts_added: tuple[str, ...] = ()
    opaque_artifacts_removed: tuple[str, ...] = ()
    opaque_artifacts_changed: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseNoteEvidence:
    """Release-note evidence collected for one package interval."""

    package: str
    from_version: str | None
    to_version: str | None
    status: str
    sources: tuple[SourceRef, ...] = ()
    summaries: tuple[str, ...] = ()
    checked_urls: tuple[str, ...] = ()
    unavailable_reason: str = ""


@dataclass(frozen=True)
class BehaviorProbeResult:
    """One deterministic behavior/contract probe result."""

    package: str
    version: str | None
    scope: str
    probe: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BehaviorDiff:
    """Observed behavior difference between current and candidate probes."""

    package: str
    from_version: str | None
    to_version: str | None
    probe: str
    severity: str
    summary: str
    before_status: str
    after_status: str


@dataclass(frozen=True)
class RunOptions:
    """Configuration for one local agent run."""

    workspace: Path
    runtime: str = "fake"
    packages: tuple[str, ...] = DEFAULT_PACKAGES
    report_dir: Path = Path("reports/sdk-evolution")
    implementation_enabled: bool = False
    refresh_preview: bool = False
    # Off by default: candidate inspection pip-installs and imports freshly
    # downloaded upstream code (see _collect_snapshots / --inspect-candidates).
    inspect_candidates: bool = False
    create_branch: bool = False
    branch_name: str | None = None
    draft_pr: bool = False
    pr_base: str | None = None
    commit_message: str = "Run SDK evolution update"
    pr_title: str = "Adapt agent-runtime-kit to upstream SDK evolution"
    model: str | None = None
    reasoning_effort: str | None = None
    codex_bin: str | None = None


@dataclass(frozen=True)
class RunContext:
    """Resolved paths and runtime metadata for one run."""

    run_id: str
    workspace: Path
    report_root: Path
    runtime: str
    event_log_path: Path
    implementation_enabled: bool
    draft_pr: bool
    event_sink: Any | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class GateResult:
    """Decision gate result."""

    allowed: bool
    reason: str


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses and paths into JSON-compatible values."""

    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [to_jsonable(item) for item in value]
    return value


def ensure_tuple(items: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    """Normalize optional string lists to tuples."""

    return tuple(items or ())
