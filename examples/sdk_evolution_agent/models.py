"""Structured data used by the SDK evolution agent."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, cast

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
class ApiSnapshot:
    """Public API snapshot for an inspected package version."""

    package: str
    version: str | None
    module: str
    requested_version: str | None = None
    observed_version: str | None = None
    provenance: str = "observed"
    members: tuple[ApiMember, ...] = ()
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
    requested_version: str | None = None
    observed_version: str | None = None
    provenance: str = "observed"


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
    mode: str | None = None
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
    allow_dirty: bool = False
    allow_cap_raise: bool = False
    commit_message: str = "Run SDK evolution update"
    pr_title: str = "Adapt agent-runtime-kit to upstream SDK evolution"


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


@dataclass(frozen=True)
class GateResult:
    """Decision gate result."""

    allowed: bool
    reason: str


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses and paths into JSON-compatible values."""

    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(cast(Any, value)))
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
