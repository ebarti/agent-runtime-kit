"""Core public models and protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from math import isfinite
from pathlib import Path
from typing import Any, Generic, NoReturn, Protocol, TypeVar, cast, runtime_checkable
from uuid import uuid4

_EnumT = TypeVar("_EnumT", bound=Enum)


class _FrozenMapping(dict[str, Any]):
    """A ``dict`` that rejects in-place mutation.

    ``MappingProxyType`` would also be read-only, but it breaks
    ``dataclasses.asdict``, ``copy.deepcopy``, ``pickle``, and ``json.dumps`` —
    all routinely applied to tasks and results by embedding applications. A
    ``dict`` subclass keeps those working (and ``isinstance(x, dict)`` true)
    while still failing loudly on writes.
    """

    def __setitem__(self, key: str, value: Any) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def __delitem__(self, key: str) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    # mypy [misc]: dict.__or__ is overloaded and an always-raising __ior__ can't
    # mirror its shape; `d |= ...` must still be blocked (it bypasses update()).
    def __ior__(self, other: Any) -> NoReturn:  # type: ignore[misc]
        raise TypeError("mapping fields on frozen models are read-only")

    def clear(self) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def pop(self, *args: Any) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def popitem(self) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def setdefault(self, *args: Any) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def update(self, *args: Any, **kwargs: Any) -> NoReturn:
        raise TypeError("mapping fields on frozen models are read-only")

    def __reduce__(self) -> tuple[Any, ...]:
        # Rebuild via the constructor: the default dict-subclass reduce protocol
        # restores items through __setitem__, which this class forbids.
        return (type(self), (dict(self),))


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return a read-only copy so a frozen model can't be mutated via a shared dict.

    The dataclasses are ``frozen=True``, but a ``Mapping`` field still stored the
    caller's dict by reference — mutating that dict afterward mutated the "frozen"
    model. Copying into a read-only dict closes that leak.
    """

    return _FrozenMapping(value)


def _coerce_enum(enum_cls: type[_EnumT], value: Any, field_name: str) -> _EnumT:
    """Coerce a raw value (typically a string literal) into an enum member.

    Boundary coercion must yield the actual member, never an equal bare string:
    the adapters compare these fields with identity (``mode is
    PermissionMode.STRICT``), so an uncoerced string would silently match
    nothing and run at the default posture.
    """

    try:
        return enum_cls(value)
    except ValueError:
        valid = ", ".join(sorted(str(member.value) for member in enum_cls))
        raise ValueError(f"invalid {field_name} {value!r}; valid values: {valid}") from None


def _require_nonblank(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _require_unique_nonblank(values: tuple[str, ...], field_name: str) -> None:
    for value in values:
        _require_nonblank(value, field_name)
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise ValueError(f"{field_name} contains duplicates: {duplicates}")


def _tuple_value(value: Any, field_name: str) -> tuple[Any, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field_name} must be a sequence, not a scalar string")
    try:
        return tuple(value)
    except TypeError as exc:
        raise ValueError(f"{field_name} must be an iterable") from exc


def _validate_optional_count(value: int | None, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a non-negative integer or None")


def _validate_optional_amount(value: float | None, field_name: str) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a non-negative finite number or None")
    if value < 0 or not isfinite(float(value)):
        raise ValueError(f"{field_name} must be a non-negative finite number or None")


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


class ReadinessStatus(str, Enum):
    """Whether a runtime can start work in the current environment."""

    READY_TO_ATTEMPT = "ready-to-attempt"
    NOT_READY = "not-ready"
    INDETERMINATE = "indeterminate"


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
    budget: bool = False
    reasoning_effort: bool = False
    network_control: bool = False
    tool_filters: bool = False
    mcp_server_env: bool = False


@dataclass(frozen=True)
class TaskSupportIssue:
    """One task field a runtime cannot honor faithfully."""

    field: str
    message: str

    def __post_init__(self) -> None:
        _require_nonblank(self.field, "TaskSupportIssue.field")
        _require_nonblank(self.message, "TaskSupportIssue.message")


@dataclass(frozen=True)
class TaskSupportReport:
    """Pure compatibility result for one task/runtime pair."""

    kind: AgentRuntimeKind | str
    issues: tuple[TaskSupportIssue, ...] = ()

    def __post_init__(self) -> None:
        issues = _tuple_value(self.issues, "TaskSupportReport.issues")
        if not all(isinstance(issue, TaskSupportIssue) for issue in issues):
            raise ValueError(
                "TaskSupportReport.issues must contain only TaskSupportIssue values"
            )
        object.__setattr__(self, "kind", AgentRuntimeKind.coerce(self.kind))
        object.__setattr__(self, "issues", issues)

    @property
    def supported(self) -> bool:
        return not self.issues


@runtime_checkable
class TaskSupportProvider(Protocol):
    """Optional extension for runtimes with provider-specific task checks.

    ``AgentRuntime`` deliberately does not require this protocol: third-party
    runtimes written before task preflight was introduced remain compatible.
    """

    def validate_task(self, task: AgentTask) -> TaskSupportReport:
        """Purely report whether this runtime can honor the task."""


@dataclass(frozen=True)
class RuntimeAvailability:
    """Side-effect-free package/loadability diagnostic for a runtime."""

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
class RuntimeReadiness:
    """Execution-readiness diagnostic produced by an explicit async probe.

    ``status`` is the readiness conclusion. ``reason`` retains the related
    package/setup category, so an installed package can legitimately produce
    ``INDETERMINATE`` with ``AvailabilityReason.AVAILABLE`` when credentials or
    provider connectivity cannot be verified safely.
    """

    kind: AgentRuntimeKind | str
    status: ReadinessStatus
    message: str
    reason: AvailabilityReason = AvailabilityReason.UNKNOWN
    package: str | None = None
    version: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", AgentRuntimeKind.coerce(self.kind))
        object.__setattr__(
            self,
            "status",
            _coerce_enum(ReadinessStatus, self.status, "RuntimeReadiness.status"),
        )
        object.__setattr__(
            self,
            "reason",
            _coerce_enum(AvailabilityReason, self.reason, "RuntimeReadiness.reason"),
        )
        _require_nonblank(self.message, "RuntimeReadiness.message")
        if self.package is not None:
            _require_nonblank(self.package, "RuntimeReadiness.package")
        if self.version is not None:
            _require_nonblank(self.version, "RuntimeReadiness.version")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("RuntimeReadiness.metadata must be a mapping")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))

    @property
    def is_ready_to_attempt(self) -> bool:
        """Return whether setup is sufficient to attempt execution."""

        return self.status is ReadinessStatus.READY_TO_ATTEMPT

    @classmethod
    def ready_to_attempt(
        cls,
        kind: AgentRuntimeKind | str,
        *,
        message: str = "ready to attempt",
        package: str | None = None,
        version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeReadiness:
        """Build a positive setup diagnostic without promising execution."""

        return cls(
            kind=kind,
            status=ReadinessStatus.READY_TO_ATTEMPT,
            reason=AvailabilityReason.AVAILABLE,
            message=message,
            package=package,
            version=version,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def not_ready(
        cls,
        kind: AgentRuntimeKind | str,
        *,
        reason: AvailabilityReason,
        message: str,
        package: str | None = None,
        version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeReadiness:
        """Build a negative readiness diagnostic."""

        return cls(
            kind=kind,
            status=ReadinessStatus.NOT_READY,
            reason=reason,
            message=message,
            package=package,
            version=version,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def indeterminate(
        cls,
        kind: AgentRuntimeKind | str,
        *,
        message: str,
        reason: AvailabilityReason = AvailabilityReason.UNKNOWN,
        package: str | None = None,
        version: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeReadiness:
        """Build a diagnostic for a probe that could not reach a conclusion."""

        return cls(
            kind=kind,
            status=ReadinessStatus.INDETERMINATE,
            reason=reason,
            message=message,
            package=package,
            version=version,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_availability(
        cls,
        availability: RuntimeAvailability,
        *,
        status: ReadinessStatus,
        message: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> RuntimeReadiness:
        """Build readiness while preserving non-metadata availability fields.

        Availability metadata is not copied implicitly because third-party
        implementations may have placed credential material there. Callers may
        pass explicitly curated readiness metadata instead.
        """

        combined_metadata = dict(metadata or {})
        fallback_messages = {
            ReadinessStatus.READY_TO_ATTEMPT: "ready to attempt",
            ReadinessStatus.NOT_READY: "not ready",
            ReadinessStatus.INDETERMINATE: "readiness could not be determined",
        }
        normalized_status = _coerce_enum(
            ReadinessStatus, status, "RuntimeReadiness.status"
        )
        if (
            normalized_status is ReadinessStatus.READY_TO_ATTEMPT
            and not availability.available
        ):
            raise ValueError("an unavailable package cannot be ready to attempt")
        return cls(
            kind=availability.kind,
            status=normalized_status,
            reason=availability.reason,
            message=message or availability.message or fallback_messages[normalized_status],
            package=availability.package,
            version=availability.version,
            metadata=combined_metadata,
        )


@runtime_checkable
class RuntimeReadinessProvider(Protocol):
    """Optional extension for runtimes that can probe execution readiness."""

    async def check_readiness(self) -> RuntimeReadiness:
        """Probe credentials/setup without executing an agent task."""


@dataclass(frozen=True)
class McpServerConfig:
    """Configuration for a stdio MCP server owned by a vendor runtime."""

    name: str
    command: str
    args: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "McpServerConfig.name")
        _require_nonblank(self.command, "McpServerConfig.command")
        object.__setattr__(self, "args", _tuple_value(self.args, "McpServerConfig.args"))
        if not all(isinstance(arg, str) for arg in self.args):
            raise ValueError("McpServerConfig.args must contain only strings")
        if not isinstance(self.env, Mapping):
            raise ValueError("McpServerConfig.env must be a mapping")
        for key, value in self.env.items():
            _require_nonblank(key, "McpServerConfig.env key")
            if not isinstance(value, str):
                raise ValueError("McpServerConfig.env values must be strings")
        object.__setattr__(self, "env", _freeze_mapping(self.env))


@dataclass(frozen=True)
class PermissionProfile:
    """Portable permission request mapped by each adapter.

    ``mode`` and ``filesystem`` also accept their string values ("strict",
    "read-only", ...) and are coerced to enum members at construction, so a
    literal that slips past type checking can never silently bypass the
    adapters' identity comparisons. Unknown values raise ``ValueError``.
    """

    mode: PermissionMode = PermissionMode.DEFAULT
    filesystem: FilesystemAccess = FilesystemAccess.WORKSPACE_WRITE
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    network: bool | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.mode, PermissionMode):
            object.__setattr__(self, "mode", _coerce_enum(PermissionMode, self.mode, "mode"))
        if not isinstance(self.filesystem, FilesystemAccess):
            object.__setattr__(
                self,
                "filesystem",
                _coerce_enum(FilesystemAccess, self.filesystem, "filesystem"),
            )
        object.__setattr__(
            self,
            "allowed_tools",
            _tuple_value(self.allowed_tools, "PermissionProfile.allowed_tools"),
        )
        object.__setattr__(
            self,
            "disallowed_tools",
            _tuple_value(self.disallowed_tools, "PermissionProfile.disallowed_tools"),
        )
        _require_unique_nonblank(self.allowed_tools, "PermissionProfile.allowed_tools")
        _require_unique_nonblank(self.disallowed_tools, "PermissionProfile.disallowed_tools")
        overlap = sorted(set(self.allowed_tools) & set(self.disallowed_tools))
        if overlap:
            raise ValueError(
                "PermissionProfile cannot both allow and disallow tools: " + ", ".join(overlap)
            )
        if self.network is not None and not isinstance(self.network, bool):
            raise ValueError("PermissionProfile.network must be bool or None")


@dataclass(frozen=True)
class ToolCallAudit:
    """Best-effort audit entry for one vendor-observed tool invocation."""

    tool_name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    status: str = "ok"
    duration_ms: int = 0

    def __post_init__(self) -> None:
        _require_nonblank(self.tool_name, "ToolCallAudit.tool_name")
        _require_nonblank(self.status, "ToolCallAudit.status")
        if (
            isinstance(self.duration_ms, bool)
            or not isinstance(self.duration_ms, int)
            or self.duration_ms < 0
        ):
            raise ValueError("ToolCallAudit.duration_ms must be a non-negative integer")
        object.__setattr__(self, "arguments", _freeze_mapping(self.arguments))


@dataclass(frozen=True)
class ArtifactRef:
    """Reference to an artifact produced by a runtime."""

    uri: str
    kind: str = "file"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.uri, "ArtifactRef.uri")
        _require_nonblank(self.kind, "ArtifactRef.kind")
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

    def __post_init__(self) -> None:
        _require_nonblank(self.session_id, "SessionResumeState.session_id")
        object.__setattr__(
            self,
            "transcript",
            _tuple_value(self.transcript, "SessionResumeState.transcript"),
        )


@dataclass(frozen=True)
class Usage:
    """Token and cost metadata reported by a runtime.

    ``input_tokens`` counts prompt tokens excluding Anthropic-style cache reads and
    cache creation, which are reported separately in ``cache_read_tokens`` and
    ``cache_creation_tokens``. Every field is ``None`` when unknown, so a reported
    zero remains distinguishable from missing telemetry.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_creation_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        _validate_optional_count(self.input_tokens, "Usage.input_tokens")
        _validate_optional_count(self.output_tokens, "Usage.output_tokens")
        _validate_optional_count(self.cache_read_tokens, "Usage.cache_read_tokens")
        _validate_optional_count(self.cache_creation_tokens, "Usage.cache_creation_tokens")
        _validate_optional_count(self.total_tokens, "Usage.total_tokens")
        _validate_optional_amount(self.cost_usd, "Usage.cost_usd")


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
        _require_nonblank(self.goal, "AgentTask.goal")
        _require_nonblank(self.task_id, "AgentTask.task_id")
        if self.model is not None:
            _require_nonblank(self.model, "AgentTask.model")
        if self.reasoning_effort is not None:
            _require_nonblank(self.reasoning_effort, "AgentTask.reasoning_effort")
        if isinstance(self.sdk_executions, bool) or not isinstance(self.sdk_executions, int):
            raise ValueError("AgentTask.sdk_executions must be a positive integer")
        if self.sdk_executions < 1:
            raise ValueError("AgentTask.sdk_executions must be a positive integer")
        _validate_optional_amount(self.budget_usd, "AgentTask.budget_usd")
        if self.session_id is not None:
            _require_nonblank(self.session_id, "AgentTask.session_id")
        if self.session_id is not None and self.resume_from is not None:
            raise ValueError("AgentTask.session_id and resume_from are mutually exclusive")
        object.__setattr__(
            self,
            "mcp_servers",
            _tuple_value(self.mcp_servers, "AgentTask.mcp_servers"),
        )
        server_names = tuple(server.name for server in self.mcp_servers)
        duplicates = sorted({name for name in server_names if server_names.count(name) > 1})
        if duplicates:
            raise ValueError(f"AgentTask.mcp_servers contains duplicate names: {duplicates}")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.output_schema is not None:
            # Local import avoids an import cycle: _schema's typed errors import
            # AgentRuntimeKind from this module.
            from agent_runtime_kit._schema import validate_output_schema

            validate_output_schema(self.output_schema)
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
    # None is both valid JSON and the historical missing-value sentinel. This
    # trailing bit preserves the distinction without shifting existing positional
    # constructor arguments.
    parsed_output_available: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.finish_reason, "AgentResult.finish_reason")
        if self.error is not None:
            _require_nonblank(self.error, "AgentResult.error")
            if self.finish_reason == FinishReason.DONE:
                raise ValueError("AgentResult.error requires a non-success finish_reason")
        if isinstance(self.rounds, bool) or not isinstance(self.rounds, int) or self.rounds < 0:
            raise ValueError("AgentResult.rounds must be a non-negative integer")
        object.__setattr__(
            self,
            "tool_calls",
            _tuple_value(self.tool_calls, "AgentResult.tool_calls"),
        )
        object.__setattr__(
            self,
            "artifacts",
            _tuple_value(self.artifacts, "AgentResult.artifacts"),
        )
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.parsed_output is not None and not self.parsed_output_available:
            object.__setattr__(self, "parsed_output_available", True)

    @property
    def cost_usd(self) -> float | None:
        """Return the reported task cost in USD."""

        return self.usage.cost_usd

    @property
    def is_success(self) -> bool:
        """Whether the runtime completed naturally without an error."""

        return self.finish_reason == FinishReason.DONE and self.error is None


_ParsedT = TypeVar("_ParsedT")


@dataclass(frozen=True)
class ParsedResult(AgentResult, Generic[_ParsedT]):
    """An ``AgentResult`` whose ``parsed_output`` was validated as ``output_type``.

    Produced by ``AgentKit.run(..., output_type=T)``. Runtime-identical to
    ``AgentResult`` — it only adds the typed accessor. ``AgentResult`` itself
    stays non-generic so existing bare ``AgentResult`` annotations remain valid
    under downstream ``disallow_any_generics`` strictness.
    """

    @property
    def parsed(self) -> _ParsedT | None:
        """The validated instance; use parsed_output_available to distinguish null."""

        return cast("_ParsedT | None", self.parsed_output)


@runtime_checkable
class AgentRuntime(Protocol):
    """Async runtime that drives an ``AgentTask`` to completion."""

    # Read-only (covariant) so a concrete adapter may narrow it to a specific
    # ``AgentRuntimeKind`` member while third-party adapters use a namespaced str.
    @property
    def kind(self) -> AgentRuntimeKind | str: ...

    capabilities: AgentCapabilities

    def availability(self) -> RuntimeAvailability:
        """Report package/loadability without credential or network probes."""

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
