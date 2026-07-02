"""Normalized runtime event helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agent_runtime_kit._types import AgentResult, AgentRuntimeKind, AgentTask, ToolCallAudit

DEFAULT_PREVIEW_CHARS = 1000
SENSITIVE_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
)
SENSITIVE_KEY_SEGMENTS = ("token",)
# Bound recursion so pathological metadata (cycles, extreme nesting) can never turn
# event construction into a RecursionError that aborts run().
_MAX_SANITIZE_DEPTH = 8
# Split camelCase so "accessToken"/"refreshToken" normalize to the same segments as
# "access_token", i.e. reach the "token" segment rule below.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def task_started_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    *,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.task.started`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "session_id": task.session_id,
        "task_goal": task.goal,
        "system_prompt": task.system,
        "working_directory": str(task.working_directory) if task.working_directory else None,
        "sdk_executions": task.sdk_executions,
        "budget_usd": task.budget_usd,
        "metadata": dict(task.metadata),
    }
    if extra:
        attrs.update(extra)
    return _event("agent.task.started", summary or f"started task {task.task_id}", attrs)


def task_completed_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    result: AgentResult,
    *,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.task.completed`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "finish_reason": result.finish_reason,
        "output": result.output,
        "rounds": result.rounds,
        "cost_usd": result.cost_usd,
        "session_id": result.session_id,
        "tool_call_count": len(result.tool_calls),
        "result_metadata": dict(result.metadata),
    }
    if extra:
        attrs.update(extra)
    return _event(
        "agent.task.completed",
        summary or f"completed task {task.task_id} ({result.finish_reason})",
        attrs,
    )


def task_failed_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    *,
    error: str,
    finish_reason: str = "failed",
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.task.failed`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "finish_reason": finish_reason,
        "error": error,
    }
    if extra:
        attrs.update(extra)
    return _event("agent.task.failed", summary or f"failed task {task.task_id}", attrs)


def output_delta_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    *,
    text: str,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.output.delta`` event."""

    preview, truncated = _preview(text)
    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "text_delta": preview,
        "text_delta_length": len(text),
        "text_delta_truncated": truncated,
    }
    if extra:
        attrs.update(extra)
    return _event("agent.output.delta", summary or f"streamed {len(text)} chars", attrs)


def tool_requested_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.tool.requested`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "tool_name": tool_name,
        "argument_count": len(arguments or {}),
        "argument_keys": sorted(str(key) for key in (arguments or {})),
    }
    if extra:
        attrs.update(extra)
    return _event("agent.tool.requested", summary or f"tool requested: {tool_name}", attrs)


def tool_completed_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    audit: ToolCallAudit,
    *,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.tool.completed`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "tool_name": audit.tool_name,
        "status": audit.status,
        "duration_ms": audit.duration_ms,
        "result_preview_length": len(audit.result_preview),
    }
    if extra:
        attrs.update(extra)
    return _event(
        "agent.tool.completed",
        summary or f"tool completed: {audit.tool_name} ({audit.status})",
        attrs,
    )


def vendor_turn_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    *,
    payload: Mapping[str, Any] | None = None,
    turn_index: int | None = None,
    summary: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ``agent.vendor.turn`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "turn_index": turn_index,
        "payload": dict(payload or {}),
    }
    if extra:
        attrs.update(extra)
    return _event("agent.vendor.turn", summary or "vendor turn update", attrs)


async def safe_emit(task: AgentTask, event: Mapping[str, Any]) -> None:
    """Best-effort event emission that never aborts a runtime."""

    if task.event_sink is None:
        return
    try:
        await task.event_sink.emit(event)
    except Exception:
        return


def _event(name: str, summary: str, attributes: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        "summary": summary,
        "attributes": _sanitize(attributes),
    }


def _sanitize(
    value: Any,
    *,
    key: str | None = None,
    _depth: int = 0,
    _seen: frozenset[int] = frozenset(),
) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[redacted]"
    if _depth >= _MAX_SANITIZE_DEPTH:
        return "[truncated: max depth]"
    if isinstance(value, Mapping | list | tuple):
        marker = id(value)
        if marker in _seen:
            return "[truncated: cycle]"
        seen = _seen | {marker}
        if isinstance(value, Mapping):
            return {
                str(item_key): _sanitize(
                    item_value, key=str(item_key), _depth=_depth + 1, _seen=seen
                )
                for item_key, item_value in value.items()
            }
        if isinstance(value, tuple):
            return tuple(_sanitize(item, _depth=_depth + 1, _seen=seen) for item in value)
        return [_sanitize(item, _depth=_depth + 1, _seen=seen) for item in value]
    if isinstance(value, str):
        preview, truncated = _preview(value)
        return f"{preview}...[truncated]" if truncated else preview
    return value


def _preview(text: str) -> tuple[str, bool]:
    if len(text) <= DEFAULT_PREVIEW_CHARS:
        return text, False
    return text[:DEFAULT_PREVIEW_CHARS], True


def _is_sensitive_key(key: str) -> bool:
    # Normalize camelCase and dashes to underscores so "accessToken", "access-token",
    # and "access_token" are treated identically.
    normalized = _CAMEL_BOUNDARY.sub("_", key).lower().replace("-", "_")
    if any(part in normalized for part in SENSITIVE_KEY_SUBSTRINGS):
        return True
    segments = normalized.split("_")
    if any(segment in segments for segment in SENSITIVE_KEY_SEGMENTS):
        return True
    # Smashed-case keys with no separator (e.g. "accesstoken", "ACCESSTOKEN") never
    # split into a bare segment, so also match a sensitive segment as a trailing
    # suffix. This still keeps plural usage counters visible ("inputtokens" ends with
    # "tokens", not "token").
    return any(normalized.endswith(segment) for segment in SENSITIVE_KEY_SEGMENTS)
