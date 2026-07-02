"""Core public models and protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only copy so a frozen model can't be mutated via a shared dict.

    The dataclasses are ``frozen=True``, but a ``Mapping`` field still stored the
    caller's dict by reference — mutating that dict afterward mutated the "frozen"
    model. Copying into a ``MappingProxyType`` closes that leak.
    """

    return MappingProxyType(dict(value))


class AgentRuntimeKind(str, Enum):
    """Supported runtime families."""

    FAKE = "fake"
    CLAUDE_AGENT_SDK = "claude-agent-sdk"
    CODEX_AGENT_SDK = "codex-agent-sdk"
    ANTIGRAVITY_AGENT_SDK = "antigravity-agent-sdk"

    @classmethod
    def coerce(cls, value: AgentRuntimeKind | str) -> AgentRuntimeKind | str:
        """Normalize a runtime kind, allowing namespaced third-party strings.

        A value matching a built-in member returns that member. Any other
        non-empty string is returned as-is so a third party can register and
        dispatch a runtime kind (e.g. ``"x-myorg-agent"``) without forking the
        enum. Empty/blank values still raise ``ValueError``.
        """

        if isinstance(value, cls):
            return value
        try:
            return cls(value)
        except ValueError:
            normalized = str(value).strip()
            if not normalized:
                raise ValueError("runtime kind must be a non-empty string") from None
            return normalized


def runtime_kind_value(value: AgentRuntimeKind | str) -> str:
    """Return the wire/string form of a runtime kind (enum member or raw string)."""

    return value.value if isinstance(value, AgentRuntimeKind) else str(value)


class AvailabilityReason(str, Enum):
    """Why a runtime is, or is not, available."""

    AVAILABLE = "available"
    MISSING_PACKAGE = "missing-package"
    MISSING_CREDENTIALS = "missing-credentials"
    UNSUPPORTED_MODEL = "unsupported-model"
    SETUP_FAILED = "setup-failed"
    UNKNOWN = "unknown"


class PermissionMode(str, Enum):
    """High-level permission intent for vendor runtimes."""

    DEFAULT = "default"
    STRICT = "strict"
    CAUTIOUS = "cautious"
    PERMISSIVE = "permissive"


class FinishReason(str, Enum):
    """Canonical ``AgentResult.finish_reason`` values across all runtimes.

    ``finish_reason`` is typed ``str`` for forward-compatibility, but the built-in
    adapters only ever emit these values. Callers can compare against these members
    (a ``str`` subclass, so ``result.finish_reason == FinishReason.FAILED`` and
    ``== "failed"`` both hold) instead of matching bare string literals.
    """

    # StrEnum semantics on every supported Python: without this, Python >= 3.11
    # renders f"{FinishReason.FAILED}" as "FinishReason.FAILED" instead of
    # "failed", leaking the enum name into event summaries and logs. Same
    # assignments CPython's own StrEnum uses; typeshed's str.__format__
    # self-type does not line up with Enum's, hence the ignore.
    __str__ = str.__str__
    __format__ = str.__format__  # type: ignore[assignment]

    DONE = "done"
    FAILED = "failed"
    MAX_TURNS = "max_turns"
    MAX_TOKENS = "max_tokens"
    INTERRUPTED = "interrupted"


class FilesystemAccess(str, Enum):
    """Filesystem mutation level requested by a task."""

    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    FULL_ACCESS = "full-access"


@runtime_checkable
class EventSink(Protocol):
    """Async destination for normalized runtime events."""

    async def emit(self, event: Mapping[str, Any]) -> None:
        """Receive one normalized event."""


@dataclass(frozen=True)
class AgentCapabilities:
    """Runtime capability advertisement."""

    mcp_support: bool = False
    working_directory: bool = False
    session_resume: bool = False
    structured_output: bool = False
    streaming: bool = False
    tool_audit: bool = False
    cancellation: bool = False


@dataclass(frozen=True)
class RuntimeAvailability:
    """Availability diagnostic for a runtime in the current environment."""

    kind: AgentRuntimeKind | str
    available: bool
    reason: AvailabilityReason = AvailabilityReason.UNKNOWN
    message: str = ""
    package: str | None = None
    version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @classmethod
    def ok(
        cls,
        kind: AgentRuntimeKind | str,
        *,
        package: str | None = None,
        version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeAvailability:
        """Build a positive availability result."""

        return cls(
            kind=AgentRuntimeKind.coerce(kind),
            available=True,
            reason=AvailabilityReason.AVAILABLE,
            message="available",
            package=package,
            version=version,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def unavailable(
        cls,
        kind: AgentRuntimeKind | str,
        *,
        reason: AvailabilityReason,
        message: str,
        package: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeAvailability:
        """Build a negative availability result."""

        return cls(
            kind=AgentRuntimeKind.coerce(kind),
            available=False,
            reason=reason,
            message=message,
            package=package,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class McpServerConfig:
    """Configuration for a stdio MCP server owned by a vendor runtime."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "env", _freeze_mapping(self.env))


@dataclass(frozen=True)
class PermissionProfile:
    """Portable permission request mapped by each adapter."""

    mode: PermissionMode = PermissionMode.DEFAULT
    filesystem: FilesystemAccess = FilesystemAccess.WORKSPACE_WRITE
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    network: bool | None = None


@dataclass(frozen=True)
class ToolCallAudit:
    """Best-effort audit entry for one vendor-observed tool invocation."""

    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    status: str = "ok"
    duration_ms: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to an artifact produced by a runtime."""

    uri: str
    kind: str = "file"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class SessionResumeState:
    """Opaque session handle carried between invocations.

    ``transcript`` is informational only: it is an opaque payload a caller may
    carry between turns. The built-in adapters do not consume it (they resume by
    ``session_id``), so populating it does not change adapter behavior.
    """

    session_id: str
    transcript: tuple[Any, ...] = ()


@dataclass(frozen=True)
class Usage:
    """Token and cost metadata reported by a runtime.

    ``input_tokens`` counts prompt tokens excluding Anthropic-style cache reads and
    cache creation, which are reported separately in ``cache_read_tokens`` and
    ``cache_creation_tokens``. ``total_tokens`` is the vendor-reported total when the
    runtime provides one, and ``None`` when it does not (so "unknown" is
    distinguishable from zero). ``cost_usd`` is ``0.0`` when the provider reports no
    cost.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    total_tokens: int | None = None
    cost_usd: float = 0.0


@dataclass(frozen=True)
class AgentTask:
    """One task dispatched to an agent runtime."""

    goal: str
    task_id: str = field(default_factory=lambda: f"task-{uuid4().hex}")
    system: str | None = None
    # First-class model / reasoning-effort selection. Adapters prefer these over the
    # legacy metadata["model"] / metadata["reasoning_effort"] aliases, which are kept
    # working for back-compat. kw_only so inserting them here does not shift the
    # positional layout that predates them (goal, task_id, system,
    # working_directory, ...).
    model: str | None = field(default=None, kw_only=True)
    reasoning_effort: str | None = field(default=None, kw_only=True)
    working_directory: Path | None = None
    mcp_servers: tuple[McpServerConfig, ...] = ()
    permissions: PermissionProfile = field(default_factory=PermissionProfile)
    event_sink: EventSink | None = None
    # Informational only: carried into task events for observability, not enforced
    # by the built-in adapters (no vendor SDK exposes a portable turn-count limit
    # this maps onto). Treated as a hint, never as a hard cap.
    sdk_executions: int = 1
    budget_usd: float | None = None
    session_id: str | None = None
    resume_from: SessionResumeState | None = None
    output_schema: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.output_schema is not None:
            object.__setattr__(self, "output_schema", _freeze_mapping(self.output_schema))


@dataclass(frozen=True)
class AgentResult:
    """Typed result returned by all runtimes."""

    output: str
    finish_reason: str = FinishReason.DONE.value
    error: str | None = None
    parsed_output: Any | None = None
    usage: Usage = field(default_factory=Usage)
    tool_calls: tuple[ToolCallAudit, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    session_id: str | None = None
    rounds: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def cost_usd(self) -> float:
        """Return the reported task cost in USD."""

        return self.usage.cost_usd


@runtime_checkable
class AgentRuntime(Protocol):
    """Async runtime that drives an ``AgentTask`` to completion."""

    # Read-only (covariant) so a concrete adapter may narrow it to a specific
    # ``AgentRuntimeKind`` member while third-party adapters use a namespaced str.
    @property
    def kind(self) -> AgentRuntimeKind | str: ...

    capabilities: AgentCapabilities

    def availability(self) -> RuntimeAvailability:
        """Report whether this runtime can execute in the current environment."""

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task."""

    async def cancel(self, task_id: str) -> None:
        """Request cancellation for a task if supported."""

    async def aclose(self) -> None:
        """Release any resources (e.g. a reused vendor process) owned by the runtime.

        Stateless runtimes may implement this as a no-op, but every runtime must
        expose it so callers can manage lifecycle uniformly without ``getattr``.
        """

    async def __aenter__(self) -> AgentRuntime:
        """Enter an async context managing this runtime's lifecycle."""

    async def __aexit__(self, exc_type: object, exc: object, tb: object, /) -> None:
        """Exit the async context, releasing resources via :meth:`aclose`.

        Parameters are positional-only so implementations may name them freely.
        """
