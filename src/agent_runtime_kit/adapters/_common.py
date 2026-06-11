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


def _extra_name(kind: AgentRuntimeKind) -> str:
    return {
        AgentRuntimeKind.CLAUDE_AGENT_SDK: "claude",
        AgentRuntimeKind.CODEX_AGENT_SDK: "codex",
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK: "antigravity",
    }.get(kind, "all")
