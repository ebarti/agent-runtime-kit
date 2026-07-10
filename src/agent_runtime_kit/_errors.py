"""Typed errors raised by agent runtimes and registries."""

from __future__ import annotations

from datetime import datetime

from agent_runtime_kit._types import AgentRuntimeKind, runtime_kind_value


class AgentRuntimeError(RuntimeError):
    """Base class for runtime-layer failures."""


class AgentRuntimeUnavailableError(AgentRuntimeError):
    """A runtime cannot be constructed or used in the current environment."""

    def __init__(self, kind: AgentRuntimeKind | str, message: str) -> None:
        self.kind: AgentRuntimeKind | str = AgentRuntimeKind.coerce(kind)
        super().__init__(message)


class AgentTaskTimeoutError(AgentRuntimeError, TimeoutError):
    """A task exceeded its absolute deadline."""

    def __init__(
        self,
        kind: AgentRuntimeKind | str,
        task_id: str,
        deadline: datetime,
    ) -> None:
        self.kind: AgentRuntimeKind | str = AgentRuntimeKind.coerce(kind)
        self.task_id = task_id
        self.deadline = deadline
        super().__init__(
            f"{runtime_kind_value(self.kind)} task {task_id!r} exceeded deadline "
            f"{deadline.isoformat()}"
        )


class UnsupportedTaskInputError(AgentRuntimeError, ValueError):
    """A runtime was asked to honor an input it does not support."""

    def __init__(self, kind: AgentRuntimeKind | str, field: str, message: str) -> None:
        self.kind: AgentRuntimeKind | str = AgentRuntimeKind.coerce(kind)
        self.field = field
        super().__init__(f"{runtime_kind_value(self.kind)} cannot honor {field}: {message}")


class OutputTypeError(AgentRuntimeError, TypeError):
    """A structured ``output_type`` cannot be honored.

    Raised when a Python type cannot be converted to a JSON schema (unsupported
    annotation — the stdlib bridge deliberately supports a bounded subset and
    fails closed on everything else), or when a returned payload does not
    conform to the requested type. Schema-generation failures surface at call
    time; payload mismatches are converted by ``AgentKit`` into a failed
    ``AgentResult`` instead of raising.
    """


class OutputSchemaError(AgentRuntimeError, ValueError):
    """An output_schema is not a valid JSON Schema."""


class RuntimeNotRegisteredError(AgentRuntimeError, LookupError):
    """No runtime factory is registered for the requested runtime kind."""

    def __init__(self, kind: AgentRuntimeKind | str) -> None:
        self.kind: AgentRuntimeKind | str = AgentRuntimeKind.coerce(kind)
        super().__init__(f"No runtime registered for {runtime_kind_value(self.kind)!r}")
