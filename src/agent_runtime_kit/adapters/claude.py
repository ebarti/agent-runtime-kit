"""Claude Agent SDK runtime adapter."""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from agent_runtime_kit._errors import AgentRuntimeUnavailableError
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
    reject_unsupported_inputs,
)
from agent_runtime_kit.events import (
    output_delta_event,
    safe_emit,
    task_completed_event,
    task_failed_event,
    task_started_event,
    tool_completed_event,
    tool_requested_event,
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
        """Execute one task with Claude Agent SDK, streaming events as they arrive."""

        await safe_emit(task, task_started_event(task, self.kind))
        model = self._model(task)
        try:
            reject_unsupported_inputs(
                self.kind, task, budget=False, network=True, tool_filters=False
            )
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            query_func, options_cls = self._load_sdk()
            options, dropped = self._build_options(task, model, options_cls)
            stream = _StreamState(self, task)
            async for message in _iter_messages(query_func(prompt=task.goal, options=options)):
                await stream.consume(message)
            result = _translate_messages(
                task,
                stream.messages,
                model=model,
                dropped_options=dropped,
                tool_results=stream.tool_results,
            )
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

        if not stream.emitted_delta and result.output:
            await safe_emit(task, output_delta_event(task, self.kind, text=result.output))
        if result.error:
            await safe_emit(
                task,
                task_failed_event(
                    task, self.kind, error=result.error, finish_reason=result.finish_reason
                ),
            )
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
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError as exc:
            raise AgentRuntimeUnavailableError(
                self.kind,
                "claude-agent-sdk is not installed. Install agent-runtime-kit[claude].",
            ) from exc
        return self._query_func or query, self._options_cls or ClaudeAgentOptions

    def _build_options(
        self, task: AgentTask, model: str, options_cls: Any
    ) -> tuple[Any, list[str]]:
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
        supported, dropped = filter_supported_kwargs(options_cls, kwargs)
        return options_cls(**supported), dropped

    def _model(self, task: AgentTask) -> str:
        return metadata_str(task.metadata, "model") or self._default_model


class _StreamState:
    """Incremental consumer that emits events as Claude messages arrive."""

    def __init__(self, runtime: ClaudeAgentRuntime, task: AgentTask) -> None:
        self._runtime = runtime
        self._task = task
        self.messages: list[Any] = []
        self.emitted_delta = False
        self._tool_names: dict[str, str] = {}
        # tool_use_id -> "ok" | "error", populated from ToolResultBlock messages.
        self.tool_results: dict[str, str] = {}

    async def consume(self, message: Any) -> None:
        self.messages.append(message)
        kind = self._runtime.kind
        task = self._task
        message_type = _message_type(message)
        if message_type in {"AssistantMessage", "assistant"}:
            for block in _iter_blocks(message):
                block_type = _message_type(block)
                if block_type in {"TextBlock", "text"}:
                    text = str(_field(block, "text", ""))
                    if text:
                        self.emitted_delta = True
                        await safe_emit(task, output_delta_event(task, kind, text=text))
                elif block_type in {"ToolUseBlock", "tool_use"}:
                    name = str(_field(block, "name", "tool"))
                    block_id = _optional_str(_field(block, "id"))
                    if block_id is not None:
                        self._tool_names[block_id] = name
                    raw_input = _field(block, "input", {})
                    arguments = raw_input if isinstance(raw_input, Mapping) else {}
                    await safe_emit(
                        task,
                        tool_requested_event(task, kind, tool_name=name, arguments=arguments),
                    )
        elif message_type in {"UserMessage", "user"}:
            for block in _iter_blocks(message):
                if _message_type(block) not in {"ToolResultBlock", "tool_result"}:
                    continue
                tool_use_id = _optional_str(_field(block, "tool_use_id"))
                status = "error" if _field(block, "is_error", False) else "ok"
                if tool_use_id is not None:
                    self.tool_results[tool_use_id] = status
                audit = ToolCallAudit(
                    tool_name=self._tool_names.get(tool_use_id or "", "tool"),
                    result_preview=str(_field(block, "content", ""))[:256],
                    status=status,
                )
                await safe_emit(task, tool_completed_event(task, kind, audit))


async def _iter_messages(candidate: Any) -> AsyncIterator[Any]:
    """Yield messages from any shape an injected query function may return.

    Tolerates: an async iterator, an awaitable resolving to one, a sync iterable,
    or a single message. Injected fakes rely on this flexibility.
    """

    if inspect.isawaitable(candidate):
        candidate = await candidate
    if hasattr(candidate, "__aiter__"):
        async for item in candidate:
            yield item
        return
    if isinstance(candidate, Iterable) and not isinstance(candidate, bytes | str | Mapping):
        for item in candidate:
            yield item
        return
    yield candidate


def _translate_messages(
    task: AgentTask,
    messages: list[Any],
    *,
    model: str,
    dropped_options: list[str],
    tool_results: Mapping[str, str],
) -> AgentResult:
    content_parts: list[str] = []
    tool_calls: list[ToolCallAudit] = []
    tool_use_ids: list[str | None] = []
    usage = Usage()
    cost_usd = 0.0
    session_id = task.session_id
    rounds = 0
    error: str | None = None
    finish_reason = "done"
    structured_output: Any | None = None

    for message in messages:
        message_type = _message_type(message)
        if message_type in {"AssistantMessage", "assistant"}:
            text, tools, ids = _assistant_content(message)
            content_parts.extend(text)
            tool_calls.extend(tools)
            tool_use_ids.extend(ids)
            session_id = _optional_str(_field(message, "session_id")) or session_id
            usage = _usage_from(_field(message, "usage"), current=usage)
            message_error = _field(message, "error")
            if message_error:
                error = str(message_error)
                finish_reason = "failed"
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
                finish_reason, error = _result_failure(message, result_text)

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
    tool_calls = _apply_tool_results(tool_calls, tool_use_ids, tool_results)
    metadata: dict[str, Any] = {"model": model, "sdk": "claude_agent_sdk"}
    if dropped_options:
        metadata["dropped_options"] = list(dropped_options)
    return AgentResult(
        output=output,
        finish_reason=finish_reason,
        error=error,
        parsed_output=structured_output,
        usage=usage,
        tool_calls=tuple(tool_calls),
        session_id=session_id,
        rounds=rounds,
        metadata=metadata,
    )


def _result_failure(message: Any, result_text: Any) -> tuple[str, str]:
    subtype = str(_field(message, "subtype", "") or "")
    finish_reason = "max_turns" if subtype == "error_max_turns" else "failed"
    errors = _field(message, "errors", ()) or ()
    joined = "; ".join(str(item) for item in errors)
    if joined:
        return finish_reason, joined
    if result_text:
        return finish_reason, str(result_text)
    return finish_reason, "Claude Agent SDK task failed"


def _apply_tool_results(
    tool_calls: list[ToolCallAudit],
    tool_use_ids: list[str | None],
    tool_results: Mapping[str, str],
) -> list[ToolCallAudit]:
    if not tool_results:
        return tool_calls
    updated: list[ToolCallAudit] = []
    for audit, tool_use_id in zip(tool_calls, tool_use_ids, strict=True):
        status = tool_results.get(tool_use_id or "")
        if status is not None and status != audit.status:
            updated.append(
                ToolCallAudit(
                    tool_name=audit.tool_name,
                    arguments=audit.arguments,
                    result_preview=audit.result_preview,
                    status=status,
                    duration_ms=audit.duration_ms,
                )
            )
        else:
            updated.append(audit)
    return updated


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


def _iter_blocks(message: Any) -> Iterable[Any]:
    content = _field(message, "content", ())
    if isinstance(content, str) or not isinstance(content, Iterable):
        return ()
    return content


def _assistant_content(
    message: Any,
) -> tuple[list[str], list[ToolCallAudit], list[str | None]]:
    content = _field(message, "content", ())
    if isinstance(content, str):
        return [content], [], []
    if not isinstance(content, Iterable):
        return [], [], []
    text_parts: list[str] = []
    tool_calls: list[ToolCallAudit] = []
    tool_use_ids: list[str | None] = []
    for block in content:
        block_type = _message_type(block)
        if block_type in {"TextBlock", "text"}:
            text_parts.append(str(_field(block, "text", "")))
        elif block_type in {"ToolUseBlock", "tool_use"}:
            name = str(_field(block, "name", "tool"))
            raw_input = _field(block, "input", {})
            arguments = raw_input if isinstance(raw_input, Mapping) else {}
            tool_calls.append(ToolCallAudit(tool_name=name, arguments=arguments))
            tool_use_ids.append(_optional_str(_field(block, "id")))
    return text_parts, tool_calls, tool_use_ids


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
