"""Shared helpers for optional vendor adapters."""

from __future__ import annotations

import inspect
import os
from collections.abc import Iterable, Mapping
from importlib import metadata
from math import isfinite
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._schema import (
    STRUCTURED_OUTPUT_MISSING as STRUCTURED_OUTPUT_MISSING,
)
from agent_runtime_kit._schema import (
    resolve_structured_output as resolve_structured_output,
)
from agent_runtime_kit._schema import validate_output_schema
from agent_runtime_kit._types import (
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    RuntimeAvailability,
    TaskSupportIssue,
)
from agent_runtime_kit.compatibility import compatibility_for


def package_availability(kind: AgentRuntimeKind) -> RuntimeAvailability:
    """Return installed-distribution availability without resolving a module."""

    compatibility = compatibility_for(kind)
    version = package_version(compatibility.package)
    if version is None:
        return RuntimeAvailability.unavailable(
            kind,
            reason=AvailabilityReason.MISSING_PACKAGE,
            message=(
                "Install the optional dependency: "
                f"agent-runtime-kit[{compatibility.extra}]"
            ),
            package=compatibility.package,
        )
    return RuntimeAvailability.ok(
        kind,
        package=compatibility.package,
        version=version,
    )


def package_version(package_name: str) -> str | None:
    """Return installed distribution version, if available."""

    try:
        return metadata.version(package_name)
    except metadata.PackageNotFoundError:
        return None


def model_support_issue(
    *,
    task: AgentTask,
    model: str,
    supported_models: tuple[str, ...] | None,
) -> TaskSupportIssue | None:
    """Report a configured model allow-list mismatch at the source field."""

    if supported_models is None or model in supported_models:
        return None
    supported = ", ".join(supported_models) or "(none)"
    if task.model is not None:
        field = "model"
    elif metadata_str(task.metadata, "model") is not None:
        field = "metadata.model"
    else:
        field = "model"
    return TaskSupportIssue(
        field,
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
        # Revalidate at dispatch: AgentTask's mapping freeze is deliberately
        # shallow, so nested schema values may have changed since construction.
        validate_output_schema(task_output_schema)
        return task_output_schema
    for key in ("output_schema", "json_schema"):
        raw = metadata_values.get(key)
        if isinstance(raw, Mapping):
            validate_output_schema(raw)
            return raw
    return None


def empty_completion_error(sdk_label: str) -> str:
    """Uniform error text when a runtime completed with nothing usable.

    "Nothing usable" means no text output, no tool calls, and no structured
    output — a completion the caller cannot act on, reported consistently across
    adapters rather than as success by some and failure by others.
    """

    return f"{sdk_label} completed with no output, tool calls, or structured output"


def filter_supported_kwargs(
    factory: Any,
    kwargs: Mapping[str, Any],
    *,
    required: Iterable[str] | Mapping[str, str] = (),
    kind: AgentRuntimeKind | str | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Split kwargs into those the callable accepts and those it does not.

    This exists to tolerate vendor option drift: a future SDK version may rename or
    remove an option this adapter builds. Rather than crash, unsupported keys are
    dropped, but drops must be observable, so the dropped key names are returned
    alongside the accepted kwargs and surfaced in ``AgentResult.metadata``.

    ``required`` names kwargs that carry mandatory task constraints (sandbox,
    approval/permission mode, tool filters, spend caps). A mapping may associate
    each kwarg with the public task field reported by ``UnsupportedTaskInputError``;
    an iterable retains the historical ``permissions`` field. Best-effort
    dropping is the wrong failure mode for either shape.

    Required keys must be explicit parameters. If a callable cannot be
    introspected, or accepts a required key only through ``**kwargs``, the adapter
    cannot prove the option will be honored and fails closed. Non-required keys
    remain best-effort under those opaque signatures.
    """

    required_fields = (
        dict(required)
        if isinstance(required, Mapping)
        else {key: "permissions" for key in required}
    )
    if required_fields and kind is None:
        raise TypeError("filter_supported_kwargs(required=...) also requires kind")
    required_keys = [key for key in required_fields if key in kwargs]
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        if required_keys:
            assert kind is not None
            raise UnsupportedTaskInputError(
                kind,
                required_fields[required_keys[0]],
                "the installed SDK callable cannot be inspected to verify "
                + ", ".join(required_keys)
                + "; refusing to run without verifiably honoring required task constraints",
            ) from None
        return dict(kwargs), []

    opaque_required = [
        key
        for key in required_keys
        if key not in signature.parameters
        or signature.parameters[key].kind is inspect.Parameter.POSITIONAL_ONLY
    ]
    if opaque_required:
        assert kind is not None
        raise UnsupportedTaskInputError(
            kind,
            required_fields[opaque_required[0]],
            "the installed SDK does not expose "
            + ", ".join(opaque_required)
            + " as explicit keyword parameters; refusing to run without a verifiable "
            "required task constraint (opaque **kwargs and positional-only parameters "
            "are insufficient)",
        )

    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return dict(kwargs), []
    supported: dict[str, Any] = {}
    dropped: list[str] = []
    for key, value in kwargs.items():
        if key in signature.parameters:
            supported[key] = value
        else:
            dropped.append(key)
    required_dropped = [key for key in required_keys if key in dropped]
    if required_dropped:
        assert kind is not None
        raise UnsupportedTaskInputError(
            kind,
            required_fields[required_dropped[0]],
            "the installed SDK does not accept "
            + ", ".join(required_dropped)
            + "; refusing to run without required task constraints",
        )
    return supported, dropped


def field_value(value: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a Mapping key or an object attribute, with a default.

    Vendor SDKs hand back both dict-like payloads and typed objects; this reads
    either shape uniformly.
    """

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def optional_int(value: Any) -> int | None:
    """Coerce a non-negative vendor count, preserving unknown as None."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        if not isfinite(value) or not value.is_integer():
            return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isinstance(value, (int, float, str)):
        try:
            if value != parsed:
                return None
        except Exception:
            return None
    return parsed if parsed >= 0 else None


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
