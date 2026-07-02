"""FastAPI-style hub over the runtime registry."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
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
)
from agent_runtime_kit.registry import RuntimeRegistry, create_default_registry

_T = TypeVar("_T")

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
        goal: str | None = None,
        task: AgentTask | None = None,
        system: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        working_directory: Path | str | None = None,
        permissions: PermissionProfile | PermissionMode | str | None = None,
        filesystem: FilesystemAccess | str | None = None,
        allowed_tools: Sequence[str] = (),
        disallowed_tools: Sequence[str] = (),
        output_schema: Mapping[str, Any] | None = None,
        event_sink: EventSink | None = None,
        mcp_servers: Sequence[McpServerConfig] = (),
        session_id: str | None = None,
        resume_from: SessionResumeState | None = None,
        budget_usd: float | None = None,
        sdk_executions: int = 1,
        task_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
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
                task_id=task_id,
                metadata=metadata,
                schema=schema,
            )
        else:
            if goal is None:
                raise ValueError("run() needs either goal=... or task=...")
            task_kwargs: dict[str, Any] = {
                "goal": goal,
                "system": system,
                "model": model,
                "reasoning_effort": reasoning_effort,
                "working_directory": _as_path(working_directory),
                "mcp_servers": tuple(mcp_servers),
                "permissions": _normalize_permissions(
                    permissions, filesystem, allowed_tools, disallowed_tools
                ),
                "event_sink": event_sink,
                "sdk_executions": sdk_executions,
                "budget_usd": budget_usd,
                "session_id": session_id,
                "resume_from": resume_from,
                "output_schema": schema,
                "metadata": dict(metadata) if metadata is not None else {},
            }
            if task_id is not None:
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
        for agent in runtimes:
            await agent.aclose()

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
        conflicting = sorted(
            name
            for name, value in field_kwargs.items()
            if value not in (None, (), {}, [])  # defaults mean "not provided"
        )
        if conflicting:
            raise ValueError(
                "task= is mutually exclusive with per-field kwargs; got both task and "
                + ", ".join(conflicting)
            )
        replacements: dict[str, Any] = {}
        if schema is not None:
            replacements["output_schema"] = schema
        if event_sink is not None:
            replacements["event_sink"] = event_sink
        if not replacements:
            return task
        values = {f.name: getattr(task, f.name) for f in dataclass_fields(task)}
        values.update(replacements)
        return AgentTask(**values)


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
    if result.error is not None or result.parsed_output is None:
        # Adapter-reported failures (including unsatisfied structured output)
        # pass through untyped; .parsed stays None.
        return cast("ParsedResult[_T]", ParsedResult(**values))
    try:
        instance = parse_as(output_type, result.parsed_output)
    except OutputTypeError as exc:
        values.update(
            finish_reason=FinishReason.FAILED.value,
            error=f"structured output does not conform to {output_type.__name__}: {exc}",
            parsed_output=None,
        )
        return cast("ParsedResult[_T]", ParsedResult(**values))
    values["parsed_output"] = instance
    return cast("ParsedResult[_T]", ParsedResult(**values))
