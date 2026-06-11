"""Core public models and protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4


class AgentRuntimeKind(str, Enum):
    """Supported runtime families."""

    FAKE = "fake"
    CLAUDE_AGENT_SDK = "claude-agent-sdk"
    CODEX_AGENT_SDK = "codex-agent-sdk"
    ANTIGRAVITY_AGENT_SDK = "antigravity-agent-sdk"

    @classmethod
    def coerce(cls, value: AgentRuntimeKind | str) -> AgentRuntimeKind:
        """Normalize a string or enum value into an ``AgentRuntimeKind``."""

        if isinstance(value, cls):
            return value
        return cls(value)


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
    sdk_turn_limit: bool = False
    cancellation: bool = False


@dataclass(frozen=True)
class RuntimeAvailability:
    """Availability diagnostic for a runtime in the current environment."""

    kind: AgentRuntimeKind
    available: bool
    reason: AvailabilityReason = AvailabilityReason.UNKNOWN
    message: str = ""
    package: str | None = None
    version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

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


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to an artifact produced by a runtime."""

    uri: str
    kind: str = "file"
    metadata: Mapping[str, Any] = field(default_factory=dict)


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


@dataclass(frozen=True)
class AgentResult:
    """Typed result returned by all runtimes."""

    output: str
    finish_reason: str = "done"
    error: str | None = None
    parsed_output: Any | None = None
    usage: Usage = field(default_factory=Usage)
    tool_calls: tuple[ToolCallAudit, ...] = ()
    artifacts: tuple[ArtifactRef, ...] = ()
    session_id: str | None = None
    rounds: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def cost_usd(self) -> float:
        """Return the reported task cost in USD."""

        return self.usage.cost_usd


@runtime_checkable
class AgentRuntime(Protocol):
    """Async runtime that drives an ``AgentTask`` to completion."""

    kind: AgentRuntimeKind
    capabilities: AgentCapabilities

    def availability(self) -> RuntimeAvailability:
        """Report whether this runtime can execute in the current environment."""

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task."""

    async def cancel(self, task_id: str) -> None:
        """Request cancellation for a task if supported."""
