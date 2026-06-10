"""Typed errors raised by agent runtimes and registries."""

from __future__ import annotations

from agent_runtime_kit._types import AgentRuntimeKind


class AgentRuntimeError(RuntimeError):
    """Base class for runtime-layer failures."""


class AgentRuntimeUnavailableError(AgentRuntimeError):
    """A runtime cannot be constructed or used in the current environment."""

    def __init__(self, kind: AgentRuntimeKind | str, message: str) -> None:
        self.kind = AgentRuntimeKind.coerce(kind)
        super().__init__(message)


class UnsupportedTaskInputError(AgentRuntimeError, ValueError):
    """A runtime was asked to honor an input it does not support."""

    def __init__(self, kind: AgentRuntimeKind | str, field: str, message: str) -> None:
        self.kind = AgentRuntimeKind.coerce(kind)
        self.field = field
        super().__init__(f"{self.kind.value} cannot honor {field}: {message}")


class RuntimeNotRegisteredError(AgentRuntimeError, LookupError):
    """No runtime factory is registered for the requested runtime kind."""

    def __init__(self, kind: AgentRuntimeKind | str) -> None:
        self.kind = AgentRuntimeKind.coerce(kind)
        super().__init__(f"No runtime registered for {self.kind.value!r}")
