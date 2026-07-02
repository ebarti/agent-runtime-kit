"""Shared helpers for optional vendor adapters."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Iterable, Mapping
from importlib import metadata, util
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    RuntimeAvailability,
)


def package_availability(
    kind: AgentRuntimeKind,
    *,
    module_name: str,
    package_name: str,
) -> RuntimeAvailability:
    """Return import/package availability without importing the package."""

    try:
        module_spec = util.find_spec(module_name)
    except ModuleNotFoundError:
        module_spec = None
    if module_spec is None:
        return RuntimeAvailability.unavailable(
            kind,
            reason=AvailabilityReason.MISSING_PACKAGE,
            message=f"Install the optional dependency: agent-runtime-kit[{_extra_name(kind)}]",
            package=package_name,
        )
    return RuntimeAvailability.ok(
        kind,
        package=package_name,
        version=package_version(package_name),
    )


def package_version(package_name: str) -> str | None:
    """Return installed distribution version, if available."""

    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None


def ensure_supported_model(
    *,
    kind: AgentRuntimeKind,
    model: str,
    supported_models: tuple[str, ...] | None,
) -> None:
    """Raise a typed error when a runtime was configured with an allow-list."""

    if supported_models is None or model in supported_models:
        return
    supported = ", ".join(supported_models)
    raise UnsupportedTaskInputError(
        kind,
        "metadata.model",
        f"model {model!r} is not supported by this runtime; supported: {supported}",
    )


def metadata_str(metadata_values: Mapping[str, Any], key: str) -> str | None:
    """Return a stripped string metadata value."""

    value = metadata_values.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def output_schema_from(
    task_output_schema: Mapping[str, Any] | None,
    metadata_values: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Resolve output schema from first-class task field or metadata aliases."""

    if task_output_schema is not None:
        return task_output_schema
    for key in ("output_schema", "json_schema"):
        raw = metadata_values.get(key)
        if isinstance(raw, Mapping):
            return raw
    return None


def parse_json_output(output: str) -> Any | None:
    """Best-effort JSON parsing for structured-output fallbacks."""

    try:
        return json.loads(output)
    except json.JSONDecodeError:
        return None


def structured_output_unsatisfied_error(sdk_label: str) -> str:
    """Uniform error text when a requested ``output_schema`` produced no JSON.

    The adapters verify JSON-parseability, not schema conformance (validation
    stays with the vendor SDK and the caller), so the message claims exactly
    that. Kept in one place so all three adapters fail identically instead of
    each inventing its own message (and, previously, its own verdict).
    """

    return f"{sdk_label} returned no parseable JSON for the requested output_schema"


def empty_completion_error(sdk_label: str) -> str:
    """Uniform error text when a runtime completed with nothing usable.

    "Nothing usable" means no text output, no tool calls, and no structured
    output — a completion the caller cannot act on, reported consistently across
    adapters rather than as success by some and failure by others.
    """

    return f"{sdk_label} completed with no output, tool calls, or structured output"


def reject_unsupported_inputs(
    kind: AgentRuntimeKind,
    task: AgentTask,
    *,
    budget: bool,
    network: bool,
    tool_filters: bool,
) -> None:
    """Raise ``UnsupportedTaskInputError`` for task fields a runtime cannot honor.

    Each flag selects a field whose silent omission would mislead the caller. The
    project contract is to reject these inputs rather than drop them quietly, so an
    adapter passes ``True`` only for fields it has no SDK surface to honor.
    """

    if budget and task.budget_usd is not None:
        raise UnsupportedTaskInputError(
            kind,
            "budget_usd",
            "this runtime does not expose a cost budget; remove budget_usd to proceed",
        )
    if network and task.permissions.network is not None:
        raise UnsupportedTaskInputError(
            kind,
            "permissions.network",
            "this runtime does not expose network access control",
        )
    if tool_filters:
        if task.permissions.allowed_tools:
            raise UnsupportedTaskInputError(
                kind,
                "permissions.allowed_tools",
                "this runtime does not expose a tool allow-list",
            )
        if task.permissions.disallowed_tools:
            raise UnsupportedTaskInputError(
                kind,
                "permissions.disallowed_tools",
                "this runtime does not expose a tool deny-list",
            )


def filter_supported_kwargs(
    factory: Any, kwargs: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Split kwargs into those the constructor accepts and those it does not.

    This exists to tolerate vendor option drift: a future SDK version may rename or
    remove an option this adapter builds. Rather than crash, unsupported keys are
    dropped, but drops must be observable, so the dropped key names are returned
    alongside the accepted kwargs and surfaced in ``AgentResult.metadata``.
    """

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return dict(kwargs), []
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return dict(kwargs), []
    supported: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in kwargs.items():
        if key in signature.parameters:
            supported[key] = value
        else:
            dropped.append(key)
    return supported, dropped


def field_value(value: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a Mapping key or an object attribute, with a default.

    Vendor SDKs hand back both dict-like payloads and typed objects; this reads
    either shape uniformly.
    """

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def optional_int(value: Any) -> int:
    """Coerce a vendor-reported value to ``int``, treating ``None``/junk as 0."""

    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def optional_str(value: Any) -> str | None:
    """Return ``str(value)`` for truthy values, else ``None``."""

    return str(value) if value else None


def fingerprint_value(value: Any) -> tuple[Any, ...]:
    """Wrap :func:`fingerprint_item` so callers always get a hashable tuple key."""

    return (fingerprint_item(value),)


def fingerprint_item(value: Any) -> Any:
    """Produce a stable, hashable fingerprint of an arbitrary vendor options object.

    Used to detect when a reusable SDK client must be restarted because its
    construction inputs changed. Handles scalars, paths, mappings, iterables,
    pydantic models (via ``model_dump``), and plain objects (via ``__dict__``),
    falling back to ``repr`` for anything opaque.
    """

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), fingerprint_item(item)) for key, item in value.items()))
    if isinstance(value, Iterable) and not isinstance(value, bytes | str | Mapping):
        return tuple(fingerprint_item(item) for item in value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return (
            type(value).__module__,
            type(value).__qualname__,
            fingerprint_item(model_dump(mode="python")),
        )
    if hasattr(value, "__dict__"):
        return (
            type(value).__module__,
            type(value).__qualname__,
            fingerprint_item(vars(value)),
        )
    return (type(value).__module__, type(value).__qualname__, repr(value))


async def close_vendor_resource(
    primary: Any | None,
    *,
    fallback: Any | None = None,
    try_disconnect: bool = False,
) -> None:
    """Close a vendor SDK resource via whatever teardown protocol it exposes.

    Tries, in order: ``disconnect()`` on ``primary`` (only when ``try_disconnect``
    is set), then ``primary.__aexit__``, then ``aclose()``/``close()`` on
    ``fallback`` (or ``primary`` when no fallback is given). Awaits any awaitable a
    close call returns. Centralizing the ladder keeps cleanup from drifting between
    adapters.
    """

    if try_disconnect and primary is not None:
        disconnect = getattr(primary, "disconnect", None)
        if callable(disconnect):
            await disconnect()
            return
    if primary is not None:
        exit_method = getattr(primary, "__aexit__", None)
        if callable(exit_method):
            await exit_method(None, None, None)
            return
    close_target = fallback if fallback is not None else primary
    close = getattr(close_target, "aclose", None) or getattr(close_target, "close", None)
    if callable(close):
        result = close()
        if hasattr(result, "__await__"):
            await result


def _extra_name(kind: AgentRuntimeKind) -> str:
    return {
        AgentRuntimeKind.CLAUDE_AGENT_SDK: "claude",
        AgentRuntimeKind.CODEX_AGENT_SDK: "codex",
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK: "antigravity",
    }.get(kind, "all")
