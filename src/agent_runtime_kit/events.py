"""Normalized runtime event helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agent_runtime_kit._types import AgentResult, AgentRuntimeKind, AgentTask, ToolCallAudit

DEFAULT_PREVIEW_CHARS = 1000
SENSITIVE_KEY_PARTS = ("api_key", "apikey", "authorization", "password", "secret", "token")


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
) -> dict[str, Any]:
    """Build an ``agent.tool.requested`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "tool_name": tool_name,
        "argument_count": len(arguments or {}),
        "argument_keys": sorted(str(key) for key in (arguments or {})),
    }
    return _event("agent.tool.requested", summary or f"tool requested: {tool_name}", attrs)


def tool_completed_event(
    task: AgentTask,
    kind: AgentRuntimeKind | str,
    audit: ToolCallAudit,
    *,
    summary: str | None = None,
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
) -> dict[str, Any]:
    """Build an ``agent.vendor.turn`` event."""

    attrs = {
        "task_id": task.task_id,
        "runtime_kind": AgentRuntimeKind.coerce(kind).value,
        "turn_index": turn_index,
        "payload": dict(payload or {}),
    }
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


def _sanitize(value: Any, *, key: str | None = None) -> Any:
    if key is not None and _is_sensitive_key(key):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(item_key): _sanitize(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, tuple):
        return tuple(_sanitize(item) for item in value)
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    if isinstance(value, str):
        preview, truncated = _preview(value)
        return f"{preview}...[truncated]" if truncated else preview
    return value


def _preview(text: str) -> tuple[str, bool]:
    if len(text) <= DEFAULT_PREVIEW_CHARS:
        return text, False
    return text[:DEFAULT_PREVIEW_CHARS], True


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)
