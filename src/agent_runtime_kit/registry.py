"""Runtime registry helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from agent_runtime_kit._errors import RuntimeNotRegisteredError
from agent_runtime_kit._runtime import FakeAgentRuntime
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentRuntime,
    AgentRuntimeKind,
    RuntimeAvailability,
)

RuntimeFactory = Callable[..., AgentRuntime]


class RuntimeRegistry:
    """Register and resolve runtime factories by kind."""

    def __init__(self) -> None:
        self._factories: dict[AgentRuntimeKind, RuntimeFactory] = {}

    def register(
        self,
        kind: AgentRuntimeKind | str,
        factory: RuntimeFactory,
        *,
        replace: bool = False,
    ) -> None:
        """Register a runtime factory."""

        normalized = AgentRuntimeKind.coerce(kind)
        if normalized in self._factories and not replace:
            raise ValueError(f"Runtime already registered for {normalized.value!r}")
        self._factories[normalized] = factory

    def unregister(self, kind: AgentRuntimeKind | str) -> None:
        """Remove a registered runtime factory."""

        normalized = AgentRuntimeKind.coerce(kind)
        self._factories.pop(normalized, None)

    def kinds(self) -> tuple[AgentRuntimeKind, ...]:
        """Return registered runtime kinds."""

        return tuple(self._factories)

    def resolve(self, kind: AgentRuntimeKind | str, **kwargs: Any) -> AgentRuntime:
        """Construct a runtime for ``kind``."""

        normalized = AgentRuntimeKind.coerce(kind)
        factory = self._factories.get(normalized)
        if factory is None:
            raise RuntimeNotRegisteredError(normalized)
        return factory(**kwargs)

    def capabilities_for(self, kind: AgentRuntimeKind | str) -> AgentCapabilities:
        """Construct a runtime and return its advertised capabilities.

        This constructs the runtime with no arguments, so any registered factory
        must be callable with zero args (the built-in adapters are).
        """

        return self.resolve(kind).capabilities

    def availability_for(self, kind: AgentRuntimeKind | str) -> RuntimeAvailability:
        """Construct a runtime and return its availability diagnostic.

        This constructs the runtime with no arguments, so any registered factory
        must be callable with zero args (the built-in adapters are).
        """

        return self.resolve(kind).availability()


def create_default_registry(
    *,
    include_fake: bool = True,
    extra_factories: Iterable[tuple[AgentRuntimeKind | str, RuntimeFactory]] = (),
) -> RuntimeRegistry:
    """Create a registry with built-in dependency-free runtimes."""

    registry = RuntimeRegistry()
    if include_fake:
        registry.register(AgentRuntimeKind.FAKE, FakeAgentRuntime)
    for kind, factory in extra_factories:
        registry.register(kind, factory)
    return registry
