"""Shared helpers for optional vendor adapters."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from importlib import metadata, util
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentRuntimeKind,
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

    if util.find_spec(module_name) is None:
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


def filter_supported_kwargs(factory: Any, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    """Drop kwargs unsupported by an injected or vendor options constructor."""

    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return dict(kwargs)
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return dict(kwargs)
    return {key: value for key, value in kwargs.items() if key in signature.parameters}


def _extra_name(kind: AgentRuntimeKind) -> str:
    return {
        AgentRuntimeKind.CLAUDE_AGENT_SDK: "claude",
        AgentRuntimeKind.CODEX_AGENT_SDK: "codex",
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK: "antigravity",
    }.get(kind, "all")
