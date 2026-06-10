"""Claude Agent SDK runtime adapter."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    PermissionMode,
    RuntimeAvailability,
    ToolCallAudit,
    Usage,
)
from agent_runtime_kit.adapters._common import (
    ensure_supported_model,
    filter_supported_kwargs,
    metadata_str,
    output_schema_from,
    package_availability,
    parse_json_output,
)
from agent_runtime_kit.events import (
    output_delta_event,
    safe_emit,
    task_completed_event,
    task_failed_event,
    task_started_event,
    tool_completed_event,
)


class ClaudeAgentRuntime:
    """Run tasks through ``claude-agent-sdk`` using the shared runtime API."""

    kind = AgentRuntimeKind.CLAUDE_AGENT_SDK
    capabilities = AgentCapabilities(
        mcp_support=True,
        working_directory=True,
        session_resume=True,
        structured_output=True,
        streaming=True,
        tool_audit=True,
        cancellation=False,
    )

    def __init__(
        self,
        *,
        default_model: str = "claude-sonnet-4-6",
        supported_models: tuple[str, ...] | None = None,
        query_func: Any | None = None,
        options_cls: Any | None = None,
    ) -> None:
        self._default_model = default_model
        self._supported_models = supported_models
        self._query_func = query_func
        self._options_cls = options_cls

    def availability(self) -> RuntimeAvailability:
        """Report Claude Agent SDK package availability."""

        if self._query_func is not None:
            return RuntimeAvailability.ok(self.kind, package="claude-agent-sdk")
        return package_availability(
            self.kind,
            module_name="claude_agent_sdk",
            package_name="claude-agent-sdk",
        )

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task with Claude Agent SDK."""

        await safe_emit(task, task_started_event(task, self.kind))
        model = self._model(task)
        try:
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            query_func, options_cls = self._load_sdk()
            options = self._build_options(task, model, options_cls)
            messages = await _collect_messages(query_func(prompt=task.goal, options=options))
            result = _translate_messages(task, messages, model=model)
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

        for tool_call in result.tool_calls:
            await safe_emit(task, tool_completed_event(task, self.kind, tool_call))
        if result.output:
            await safe_emit(task, output_delta_event(task, self.kind, text=result.output))
        if result.error:
            await safe_emit(task, task_failed_event(task, self.kind, error=result.error))
        else:
            await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> None:
        """Claude ``query`` calls do not expose a portable cancellation handle."""

        del task_id

    def _load_sdk(self) -> tuple[Any, Any]:
        if self._query_func is not None and self._options_cls is not None:
            return self._query_func, self._options_cls
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "claude-agent-sdk is not installed. Install agent-runtime-kit[claude]."
            ) from exc
        return self._query_func or query, self._options_cls or ClaudeAgentOptions

    def _build_options(self, task: AgentTask, model: str, options_cls: Any) -> Any:
        metadata = task.metadata
        kwargs: dict[str, Any] = {
            "model": model,
            "allowed_tools": list(task.permissions.allowed_tools),
            "disallowed_tools": list(task.permissions.disallowed_tools),
            "permission_mode": _permission_mode(task.permissions.mode),
        }
        if task.system:
            kwargs["system_prompt"] = task.system
        if task.working_directory is not None:
            kwargs["cwd"] = task.working_directory
        if task.mcp_servers:
            kwargs["mcp_servers"] = {
                server.name: {
                    "type": "stdio",
                    "command": server.command,
                    "args": list(server.args),
                    "env": dict(server.env),
                }
                for server in task.mcp_servers
            }
        if task.resume_from is not None:
            kwargs["resume"] = task.resume_from.session_id
        elif task.session_id:
            kwargs["resume"] = task.session_id
        if task.budget_usd is not None:
            kwargs["max_budget_usd"] = task.budget_usd
        output_schema = output_schema_from(task.output_schema, metadata)
        if output_schema is not None:
            kwargs["output_format"] = {"type": "json_schema", "schema": dict(output_schema)}
        setting_sources = metadata.get("setting_sources")
        if isinstance(setting_sources, list):
            kwargs["setting_sources"] = [str(item) for item in setting_sources]
        return options_cls(**filter_supported_kwargs(options_cls, kwargs))

    def _model(self, task: AgentTask) -> str:
        return metadata_str(task.metadata, "model") or self._default_model


async def _collect_messages(candidate: Any) -> list[Any]:
    if inspect.isawaitable(candidate):
        candidate = await candidate
    if hasattr(candidate, "__aiter__"):
        return [message async for message in _as_async_iter(candidate)]
    if isinstance(candidate, Iterable) and not isinstance(candidate, bytes | str | Mapping):
        return list(candidate)
    return [candidate]


async def _as_async_iter(candidate: AsyncIterator[Any]) -> AsyncIterator[Any]:
    async for item in candidate:
        yield item


def _translate_messages(task: AgentTask, messages: list[Any], *, model: str) -> AgentResult:
    content_parts: list[str] = []
    tool_calls: list[ToolCallAudit] = []
    usage = Usage()
    cost_usd = 0.0
    session_id = task.session_id
    rounds = 0
    error: str | None = None
    structured_output: Any | None = None

    for message in messages:
        message_type = _message_type(message)
        if message_type in {"AssistantMessage", "assistant"}:
            text, tools = _assistant_content(message)
            content_parts.extend(text)
            tool_calls.extend(tools)
            session_id = _optional_str(_field(message, "session_id")) or session_id
            usage = _usage_from(_field(message, "usage"), current=usage)
            message_error = _field(message, "error")
            if message_error:
                error = str(message_error)
        elif message_type in {"ResultMessage", "result"}:
            result_text = _field(message, "result")
            if result_text and not content_parts:
                content_parts.append(str(result_text))
            structured_output = _field(message, "structured_output", structured_output)
            cost_usd = float(_field(message, "total_cost_usd", cost_usd) or cost_usd)
            usage = _usage_from(_field(message, "usage"), current=usage)
            rounds = int(_field(message, "num_turns", rounds) or rounds)
            session_id = _optional_str(_field(message, "session_id")) or session_id
            if _field(message, "is_error", False):
                errors = _field(message, "errors", ()) or ()
                error = "; ".join(str(item) for item in errors) or "Claude Agent SDK task failed"
        elif isinstance(message, Mapping):
            if message.get("content"):
                content_parts.append(str(message["content"]))
            if message.get("error"):
                error = str(message["error"])

    output = "\n".join(part for part in content_parts if part).strip()
    if (
        structured_output is None
        and output_schema_from(task.output_schema, task.metadata) is not None
    ):
        structured_output = parse_json_output(output)
    usage = Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=cost_usd,
    )
    return AgentResult(
        output=output,
        finish_reason="failed" if error else "done",
        error=error,
        parsed_output=structured_output,
        usage=usage,
        tool_calls=tuple(tool_calls),
        session_id=session_id,
        rounds=rounds,
        metadata={"model": model, "sdk": "claude_agent_sdk"},
    )


def _permission_mode(mode: PermissionMode) -> str:
    if mode is PermissionMode.STRICT:
        return "plan"
    if mode is PermissionMode.CAUTIOUS:
        return "acceptEdits"
    if mode is PermissionMode.PERMISSIVE:
        return "bypassPermissions"
    return "default"


def _message_type(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("type") or "")
    return type(message).__name__


def _assistant_content(message: Any) -> tuple[list[str], list[ToolCallAudit]]:
    content = _field(message, "content", ())
    if isinstance(content, str):
        return [content], []
    if not isinstance(content, Iterable):
        return [], []
    text_parts: list[str] = []
    tool_calls: list[ToolCallAudit] = []
    for block in content:
        block_type = _message_type(block)
        if block_type in {"TextBlock", "text"}:
            text_parts.append(str(_field(block, "text", "")))
        elif block_type in {"ToolUseBlock", "tool_use"}:
            name = str(_field(block, "name", "tool"))
            raw_input = _field(block, "input", {})
            arguments = raw_input if isinstance(raw_input, Mapping) else {}
            tool_calls.append(ToolCallAudit(tool_name=name, arguments=arguments))
    return text_parts, tool_calls


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_from(value: Any, *, current: Usage) -> Usage:
    if not isinstance(value, Mapping):
        return current
    input_tokens = int(value.get("input_tokens") or current.input_tokens)
    output_tokens = int(value.get("output_tokens") or current.output_tokens)
    cache_creation = int(value.get("cache_creation_input_tokens") or current.cache_creation_tokens)
    cache_read = int(value.get("cache_read_input_tokens") or current.cache_read_tokens)
    total = input_tokens + output_tokens + cache_creation + cache_read
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        total_tokens=total,
        cost_usd=current.cost_usd,
    )


def _optional_str(value: Any) -> str | None:
    return str(value) if value else None
