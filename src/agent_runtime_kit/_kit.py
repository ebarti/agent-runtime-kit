"""FastAPI-style hub over the runtime registry."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import fields as dataclass_fields
from pathlib import Path
from typing import Any, TypeVar, cast, overload

from agent_runtime_kit._errors import OutputTypeError
from agent_runtime_kit._schema import json_schema_for, parse_as
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    EventSink,
    FilesystemAccess,
    FinishReason,
    McpServerConfig,
    ParsedResult,
    PermissionMode,
    PermissionProfile,
    RuntimeAvailability,
    SessionResumeState,
    TaskSupportReport,
)
from agent_runtime_kit.registry import RuntimeFactory, RuntimeRegistry, create_default_registry
from agent_runtime_kit.support import validate_task as validate_runtime_task

_T = TypeVar("_T")
# An event handler receives one normalized event dict; sync or async.
_EventHandler = Callable[[Mapping[str, Any]], Any]
_HandlerT = TypeVar("_HandlerT", bound=_EventHandler)
_FactoryT = TypeVar("_FactoryT", bound=RuntimeFactory)


class _UnsetType:
    """Sentinel whose repr keeps the public signature compact."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "..."


_UNSET = _UnsetType()

# Short spellings for the built-in kinds, resolved only by AgentKit (the
# registry itself stays alias-free; the full kind strings always work).
KIND_ALIASES: dict[str, AgentRuntimeKind] = {
    "fake": AgentRuntimeKind.FAKE,
    "claude": AgentRuntimeKind.CLAUDE_AGENT_SDK,
    "codex": AgentRuntimeKind.CODEX_AGENT_SDK,
    "antigravity": AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
}


class AgentKit:
    """One object to hold: registry, runtimes, and a kwargs-native ``run``.

    ``AgentKit()`` builds a registry with the fake runtime and the vendor
    adapters registered (adapters resolve their SDKs lazily, so this works
    without any extra installed — ``availability_for`` reports what is
    missing). Pass ``registry=`` to bring your own; the other flags then do
    not apply.

    Runtimes resolved through the hub are constructed zero-arg, cached per
    kind, and closed by ``aclose()`` / ``async with``. A custom-configured
    adapter instance can be passed directly to ``run`` instead of a kind.
    """

    def __init__(
        self,
        *,
        registry: RuntimeRegistry | None = None,
        include_fake: bool = True,
        register_default_adapters: bool = True,
    ) -> None:
        if registry is None:
            registry = create_default_registry(include_fake=include_fake)
            if register_default_adapters:
                # Imported here, not at module scope: the adapters import the
                # top-level package, which imports this module.
                from agent_runtime_kit.adapters import register_adapters

                register_adapters(registry)
        self._registry = registry
        self._runtimes: dict[AgentRuntimeKind | str, AgentRuntime] = {}
        self._handlers: list[tuple[str, _EventHandler]] = []
        self._cache_lock = asyncio.Lock()

    @property
    def registry(self) -> RuntimeRegistry:
        return self._registry

    def kinds(self) -> tuple[AgentRuntimeKind | str, ...]:
        return self._registry.kinds()

    def availability(self) -> tuple[RuntimeAvailability, ...]:
        """Availability diagnostics for every registered kind."""

        return tuple(self._registry.availability_for(kind) for kind in self._registry.kinds())

    def availability_for(self, kind: AgentRuntimeKind | str) -> RuntimeAvailability:
        return self._registry.availability_for(self._normalize_kind(kind))

    def capabilities_for(self, kind: AgentRuntimeKind | str) -> AgentCapabilities:
        return self._registry.capabilities_for(self._normalize_kind(kind))

    def validate_task(
        self,
        runtime: AgentRuntimeKind | str | AgentRuntime,
        task: AgentTask,
    ) -> TaskSupportReport:
        """Report unsupported task fields without starting a run."""

        if isinstance(runtime, str):
            kind = self._normalize_kind(runtime)
            cached = self._runtimes.get(kind)
            if cached is None:
                return self._registry.validate_task_for(kind, task)
            runtime = cached
        return validate_runtime_task(runtime, task)

    def on(self, event: str = "*") -> Callable[[_HandlerT], _HandlerT]:
        """Register an event handler for tasks assembled by this hub.

        ``event`` is an exact normalized event name (``"agent.tool.completed"``,
        ...) or ``"*"`` for everything. Handlers may be sync or async and their
        exceptions are swallowed — the ``safe_emit`` contract: observability
        must never break a run. Registration applies to runs started after it.
        """

        def register(handler: _HandlerT) -> _HandlerT:
            self._handlers.append((event, handler))
            return handler

        return register

    def runtime(
        self, kind: AgentRuntimeKind | str, *, replace: bool = False
    ) -> Callable[[_FactoryT], _FactoryT]:
        """Register a runtime factory under ``kind`` (decorator form).

        The factory must remain constructible with zero arguments:
        ``capabilities_for``/``availability_for`` build it that way.
        """

        def register(factory: _FactoryT) -> _FactoryT:
            self._registry.register(kind, factory, replace=replace)
            return factory

        return register

    @overload
    async def run(
        self,
        runtime: AgentRuntimeKind | str | AgentRuntime,
        *,
        output_type: type[_T],
        goal: str | None = ...,
        task: AgentTask | None = ...,
        system: str | None = ...,
        model: str | None = ...,
        reasoning_effort: str | None = ...,
        working_directory: Path | str | None = ...,
        permissions: PermissionProfile | PermissionMode | str | None = ...,
        filesystem: FilesystemAccess | str | None = ...,
        allowed_tools: Sequence[str] = ...,
        disallowed_tools: Sequence[str] = ...,
        output_schema: Mapping[str, Any] | None = ...,
        event_sink: EventSink | None = ...,
        mcp_servers: Sequence[McpServerConfig] = ...,
        session_id: str | None = ...,
        resume_from: SessionResumeState | None = ...,
        budget_usd: float | None = ...,
        sdk_executions: int = ...,
        task_id: str | None = ...,
        metadata: Mapping[str, Any] | None = ...,
    ) -> ParsedResult[_T]: ...

    @overload
    async def run(
        self,
        runtime: AgentRuntimeKind | str | AgentRuntime,
        *,
        output_type: None = None,
        goal: str | None = ...,
        task: AgentTask | None = ...,
        system: str | None = ...,
        model: str | None = ...,
        reasoning_effort: str | None = ...,
        working_directory: Path | str | None = ...,
        permissions: PermissionProfile | PermissionMode | str | None = ...,
        filesystem: FilesystemAccess | str | None = ...,
        allowed_tools: Sequence[str] = ...,
        disallowed_tools: Sequence[str] = ...,
        output_schema: Mapping[str, Any] | None = ...,
        event_sink: EventSink | None = ...,
        mcp_servers: Sequence[McpServerConfig] = ...,
        session_id: str | None = ...,
        resume_from: SessionResumeState | None = ...,
        budget_usd: float | None = ...,
        sdk_executions: int = ...,
        task_id: str | None = ...,
        metadata: Mapping[str, Any] | None = ...,
    ) -> AgentResult: ...

    async def run(
        self,
        runtime: AgentRuntimeKind | str | AgentRuntime,
        *,
        output_type: type[Any] | None = None,
        goal: str | None | _UnsetType = _UNSET,
        task: AgentTask | None = None,
        system: str | None | _UnsetType = _UNSET,
        model: str | None | _UnsetType = _UNSET,
        reasoning_effort: str | None | _UnsetType = _UNSET,
        working_directory: Path | str | None | _UnsetType = _UNSET,
        permissions: PermissionProfile | PermissionMode | str | None | _UnsetType = _UNSET,
        filesystem: FilesystemAccess | str | None | _UnsetType = _UNSET,
        allowed_tools: Sequence[str] | _UnsetType = _UNSET,
        disallowed_tools: Sequence[str] | _UnsetType = _UNSET,
        output_schema: Mapping[str, Any] | None = None,
        event_sink: EventSink | None = None,
        mcp_servers: Sequence[McpServerConfig] | _UnsetType = _UNSET,
        session_id: str | None | _UnsetType = _UNSET,
        resume_from: SessionResumeState | None | _UnsetType = _UNSET,
        budget_usd: float | None | _UnsetType = _UNSET,
        sdk_executions: int | _UnsetType = _UNSET,
        task_id: str | None | _UnsetType = _UNSET,
        metadata: Mapping[str, Any] | None | _UnsetType = _UNSET,
    ) -> AgentResult:
        """Run one task, assembling the ``AgentTask`` from keyword arguments.

        Exactly one of ``goal`` (plus the other field kwargs) or ``task`` must
        be provided; ``output_type``, ``output_schema``, and ``event_sink``
        may accompany a prebuilt ``task`` and override its fields.
        ``output_type`` derives ``output_schema`` from a Python type and
        validates the result's ``parsed_output`` into it; a payload that does
        not conform yields a ``finish_reason="failed"`` result (the same
        convention the adapters use for unsatisfied structured output), never
        an exception.
        """

        if output_type is not None and output_schema is not None:
            raise ValueError("output_type and output_schema are mutually exclusive")
        schema = json_schema_for(output_type) if output_type is not None else output_schema

        if task is not None:
            built = self._merge_into_task(
                task,
                goal=goal,
                system=system,
                model=model,
                reasoning_effort=reasoning_effort,
                working_directory=working_directory,
                permissions=permissions,
                filesystem=filesystem,
                allowed_tools=allowed_tools,
                disallowed_tools=disallowed_tools,
                event_sink=event_sink,
                mcp_servers=mcp_servers,
                session_id=session_id,
                resume_from=resume_from,
                budget_usd=budget_usd,
                sdk_executions=sdk_executions,
                task_id=task_id,
                metadata=metadata,
                schema=schema,
            )
        else:
            if goal is _UNSET or goal is None:
                raise ValueError("run() needs either goal=... or task=...")
            normalized_permissions = cast(
                "PermissionProfile | PermissionMode | str | None",
                None if permissions is _UNSET else permissions,
            )
            normalized_filesystem = cast(
                "FilesystemAccess | str | None",
                None if filesystem is _UNSET else filesystem,
            )
            normalized_allowed_tools = cast(
                "Sequence[str]", () if allowed_tools is _UNSET else allowed_tools
            )
            normalized_disallowed_tools = cast(
                "Sequence[str]", () if disallowed_tools is _UNSET else disallowed_tools
            )
            normalized_working_directory = cast(
                "Path | str | None",
                None if working_directory is _UNSET else working_directory,
            )
            normalized_mcp_servers = cast(
                "Sequence[McpServerConfig]", () if mcp_servers is _UNSET else mcp_servers
            )
            normalized_metadata = cast(
                "Mapping[str, Any] | None", None if metadata is _UNSET else metadata
            )
            task_kwargs: dict[str, Any] = {
                "goal": goal,
                "system": None if system is _UNSET else system,
                "model": None if model is _UNSET else model,
                "reasoning_effort": None if reasoning_effort is _UNSET else reasoning_effort,
                "working_directory": _as_path(normalized_working_directory),
                "mcp_servers": tuple(normalized_mcp_servers),
                "permissions": _normalize_permissions(
                    normalized_permissions,
                    normalized_filesystem,
                    normalized_allowed_tools,
                    normalized_disallowed_tools,
                ),
                "event_sink": self._compose_sink(event_sink),
                "sdk_executions": 1 if sdk_executions is _UNSET else sdk_executions,
                "budget_usd": None if budget_usd is _UNSET else budget_usd,
                "session_id": None if session_id is _UNSET else session_id,
                "resume_from": None if resume_from is _UNSET else resume_from,
                "output_schema": schema,
                "metadata": dict(normalized_metadata) if normalized_metadata is not None else {},
            }
            if task_id is not _UNSET and task_id is not None:
                task_kwargs["task_id"] = task_id
            built = AgentTask(**task_kwargs)

        agent = runtime if not isinstance(runtime, str) else await self._runtime_for(runtime)
        result = await agent.run(built)
        if output_type is None:
            return result
        return _parse_result(output_type, result)

    async def aclose(self) -> None:
        """Close every runtime this hub constructed and cached."""

        async with self._cache_lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        first_error: BaseException | None = None
        for agent in runtimes:
            try:
                await agent.aclose()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    async def __aenter__(self) -> AgentKit:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()

    def _normalize_kind(self, kind: AgentRuntimeKind | str) -> AgentRuntimeKind | str:
        if isinstance(kind, str) and not isinstance(kind, AgentRuntimeKind):
            alias = KIND_ALIASES.get(kind)
            if alias is not None:
                return alias
        return AgentRuntimeKind.coerce(kind)

    async def _runtime_for(self, kind: AgentRuntimeKind | str) -> AgentRuntime:
        normalized = self._normalize_kind(kind)
        async with self._cache_lock:
            agent = self._runtimes.get(normalized)
            if agent is None:
                agent = self._registry.resolve(normalized)
                self._runtimes[normalized] = agent
            return agent

    def _merge_into_task(
        self,
        task: AgentTask,
        *,
        schema: Mapping[str, Any] | None,
        event_sink: EventSink | None,
        **field_kwargs: Any,
    ) -> AgentTask:
        conflicting = sorted(name for name, value in field_kwargs.items() if value is not _UNSET)
        if conflicting:
            raise ValueError(
                "task= is mutually exclusive with per-field kwargs; got both task and "
                + ", ".join(conflicting)
            )
        replacements: dict[str, Any] = {}
        if schema is not None:
            replacements["output_schema"] = schema
        effective_sink = event_sink if event_sink is not None else task.event_sink
        composed_sink = self._compose_sink(effective_sink)
        if composed_sink is not task.event_sink:
            replacements["event_sink"] = composed_sink
        if not replacements:
            return task
        values = {f.name: getattr(task, f.name) for f in dataclass_fields(task)}
        values.update(replacements)
        return AgentTask(**values)

    def _compose_sink(self, downstream: EventSink | None) -> EventSink | None:
        if not self._handlers:
            return downstream
        return _TeeSink(tuple(self._handlers), downstream)


class _TeeSink:
    """Fan events out to hub handlers, then to the task's own sink.

    Handler and downstream failures are swallowed independently (the
    ``safe_emit`` contract): observability must never break a run, and one bad
    handler must not starve the others or the downstream sink.
    """

    def __init__(
        self,
        handlers: tuple[tuple[str, _EventHandler], ...],
        downstream: EventSink | None,
    ) -> None:
        self._handlers = handlers
        self._downstream = downstream

    async def emit(self, event: Mapping[str, Any]) -> None:
        name = str(event.get("name", ""))
        for pattern, handler in self._handlers:
            if pattern != "*" and pattern != name:
                continue
            try:
                outcome = handler(event)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception:
                continue
        if self._downstream is not None:
            try:
                await self._downstream.emit(event)
            except Exception:
                return


def _as_path(value: Path | str | None) -> Path | None:
    if value is None or isinstance(value, Path):
        return value
    return Path(value)


def _normalize_permissions(
    permissions: PermissionProfile | PermissionMode | str | None,
    filesystem: FilesystemAccess | str | None,
    allowed_tools: Sequence[str],
    disallowed_tools: Sequence[str],
) -> PermissionProfile:
    if isinstance(permissions, PermissionProfile):
        if filesystem is not None or allowed_tools or disallowed_tools:
            raise ValueError(
                "pass filesystem/allowed_tools/disallowed_tools inside the "
                "PermissionProfile, not alongside one"
            )
        return permissions
    # Strings coerce to enum members in PermissionProfile.__post_init__.
    profile_kwargs: dict[str, Any] = {
        "allowed_tools": tuple(allowed_tools),
        "disallowed_tools": tuple(disallowed_tools),
    }
    if permissions is not None:
        profile_kwargs["mode"] = permissions
    if filesystem is not None:
        profile_kwargs["filesystem"] = filesystem
    return PermissionProfile(**profile_kwargs)


def _parse_result(output_type: type[_T], result: AgentResult) -> ParsedResult[_T]:
    values = {f.name: getattr(result, f.name) for f in dataclass_fields(result)}
    if result.error is not None or result.finish_reason == FinishReason.FAILED:
        # Never expose an adapter's unvalidated raw payload through the typed
        # ``ParsedResult`` surface when the adapter itself reported failure.
        values["parsed_output"] = None
        values["parsed_output_available"] = False
        return cast("ParsedResult[_T]", ParsedResult(**values))
    if not result.parsed_output_available:
        return cast("ParsedResult[_T]", ParsedResult(**values))
    try:
        instance = parse_as(output_type, result.parsed_output)
    except OutputTypeError as exc:
        values.update(
            finish_reason=FinishReason.FAILED.value,
            error=f"structured output does not conform to {output_type.__name__}: {exc}",
            parsed_output=None,
            parsed_output_available=False,
        )
        return cast("ParsedResult[_T]", ParsedResult(**values))
    values["parsed_output"] = instance
    return cast("ParsedResult[_T]", ParsedResult(**values))
