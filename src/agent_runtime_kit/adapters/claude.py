"""Claude Agent SDK runtime adapter."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
from collections.abc import AsyncIterator, Iterable, Mapping
from math import isfinite
from typing import Any

from agent_runtime_kit._control import RuntimeTaskController
from agent_runtime_kit._errors import AgentRuntimeUnavailableError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    CancellationReceipt,
    FilesystemAccess,
    PermissionMode,
    PermissionProfile,
    ReadinessStatus,
    RuntimeAvailability,
    RuntimeReadiness,
    TaskSupportReport,
    ToolCallAudit,
    Usage,
)
from agent_runtime_kit.adapters._common import (
    STRUCTURED_OUTPUT_MISSING,
    VendorCleanupQuarantine,
    close_vendor_resource,
    empty_completion_error,
    field_value,
    filter_supported_kwargs,
    fingerprint_value,
    finish_vendor_cleanup,
    metadata_str,
    model_support_issue,
    optional_int,
    optional_str,
    output_schema_from,
    package_availability,
    resolve_structured_output,
    select_model,
    validate_model_configuration,
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
from agent_runtime_kit.support import _validate_declared_task_support, require_task_support

logger = logging.getLogger(__name__)


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
        cancellation=True,
        budget=True,
        reasoning_effort=True,
        tool_filters=True,
        mcp_server_env=True,
    )

    def __init__(
        self,
        *,
        default_model: str | None = None,
        supported_models: tuple[str, ...] | None = None,
        env: Mapping[str, str] | None = None,
        query_func: Any | None = None,
        options_cls: Any | None = None,
        client_cls: Any | None = None,
        reuse_process: bool = False,
    ) -> None:
        self._default_model = default_model
        self._supported_models = validate_model_configuration(
            default_model, supported_models
        )
        self._env = dict(env) if env is not None else None
        self._query_func = query_func
        self._options_cls = options_cls
        self._client_cls = client_cls
        self._reuse_process = reuse_process
        self._client: Any | None = None
        self._client_key: tuple[Any, ...] | None = None
        self._sdk_process_start_count = 0
        self._sdk_process_reuse_count = 0
        self._client_lock = asyncio.Lock()
        self._client_run_lock = asyncio.Lock()
        self._task_controller = RuntimeTaskController(self.kind)
        self._cleanup_quarantine = VendorCleanupQuarantine()

    async def __aenter__(self) -> ClaudeAgentRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()

    def availability(self) -> RuntimeAvailability:
        """Report Claude package presence without probing credentials."""

        if self._query_func is not None or self._client_cls is not None:
            return RuntimeAvailability.ok(self.kind, package="claude-agent-sdk")
        return package_availability(self.kind)

    async def check_readiness(self) -> RuntimeReadiness:
        """Inspect supported direct credential signals without starting Claude."""

        availability = self.availability()
        if not availability.available:
            return RuntimeReadiness.from_availability(
                availability,
                status=ReadinessStatus.NOT_READY,
            )
        auth_metadata = _claude_auth_metadata(self._env)
        if auth_metadata["auth_source"] in {
            "anthropic-api-key",
            "claude-code-oauth-token",
        } or auth_metadata.get("bedrock_api_key_configured") is True:
            return RuntimeReadiness.ready_to_attempt(
                self.kind,
                message="A direct Claude credential signal is configured.",
                package=availability.package,
                version=availability.version,
                metadata=auth_metadata,
            )
        return RuntimeReadiness.indeterminate(
            self.kind,
            reason=AvailabilityReason.AVAILABLE,
            message=(
                "The Claude package is installed, but provider-chain or local-login "
                "credentials cannot be verified without starting a task."
            ),
            package=availability.package,
            version=availability.version,
            metadata=auth_metadata,
        )

    def validate_task(self, task: AgentTask) -> TaskSupportReport:
        """Report unsupported fields without loading the vendor SDK."""

        report = _validate_declared_task_support(self.kind, self.capabilities, task)
        selection = select_model(task, self._default_model)
        issue = model_support_issue(
            selection=selection,
            supported_models=self._supported_models,
        )
        return TaskSupportReport(
            kind=self.kind,
            issues=report.issues + ((issue,) if issue is not None else ()),
        )

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one deadline- and cancellation-controlled Claude task."""

        self._cleanup_quarantine.ensure_ready(self.kind)
        return await self._task_controller.run(task, lambda: self._run_task(task))

    async def _run_task(self, task: AgentTask) -> AgentResult:
        """Execute one task with Claude Agent SDK, streaming events as they arrive."""

        await safe_emit(task, task_started_event(task, self.kind))
        selection = select_model(task, self._default_model)
        model = selection.value
        try:
            require_task_support(self.validate_task(task))
            query_func, options_cls, client_cls = self._load_sdk()
            options, dropped = self._build_options(task, model, options_cls)
            stream = _StreamState(self, task)
            process_reused = False
            if self._reuse_process:
                process_reused = await self._run_with_client(
                    task,
                    options=options,
                    client_cls=client_cls,
                    stream=stream,
                )
            else:
                async for message in _iter_messages(
                    query_func(prompt=task.goal, options=options)
                ):
                    await stream.consume(message)
            result = _translate_messages(
                task,
                stream.messages,
                model=model,
                model_source=selection.source,
                dropped_options=dropped,
                tool_results=stream.tool_results,
                tool_previews=stream.tool_previews,
                permission_mode=_effective_permission_mode(task.permissions),
                process_metadata=(
                    self._process_reuse_metadata(process_reused)
                    if self._reuse_process
                    else None
                ),
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

    async def cancel(self, task_id: str) -> CancellationReceipt:
        """Request cancellation at the adapter coroutine boundary."""

        return await self._task_controller.cancel(task_id)

    async def aclose(self) -> None:
        """Close any reusable Claude SDK client process owned by this runtime.

        Acquires the run lock first so an external ``aclose()`` waits for an
        in-flight ``run()`` to finish instead of closing the client mid-stream.
        """

        self._cleanup_quarantine.ensure_ready(self.kind)
        async with self._client_run_lock:
            await self._close_client()

    async def _close_client(self) -> None:
        """Close the client holding only the client lock (run lock assumed free)."""

        async with self._client_lock:
            await self._close_client_locked()

    def _load_sdk(self) -> tuple[Any, Any, Any]:
        if (
            self._query_func is not None
            and self._options_cls is not None
            and (not self._reuse_process or self._client_cls is not None)
        ):
            return self._query_func, self._options_cls, self._client_cls
        try:  # pragma: no cover - real SDK import, exercised via injected fakes in tests
            from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, query
        except ImportError as exc:  # pragma: no cover
            raise AgentRuntimeUnavailableError(
                self.kind,
                "claude-agent-sdk is not installed. Install agent-runtime-kit[claude].",
            ) from exc
        return (  # pragma: no cover
            self._query_func or query,
            self._options_cls or ClaudeAgentOptions,
            self._client_cls or ClaudeSDKClient,
        )

    async def _run_with_client(
        self,
        task: AgentTask,
        *,
        options: Any,
        client_cls: Any,
        stream: _StreamState,
    ) -> bool:
        async with self._client_run_lock:
            # Scope reuse by conversation identity so two tasks with identical
            # options but different sessions never share one client; no-session
            # tasks (conversation id None) may still share, as before.
            key = (fingerprint_value(options), _conversation_id(task))
            try:
                client, process_reused = await self._persistent_client(
                    client_cls,
                    options,
                    key=key,
                )
                await client.query(task.goal, session_id=_client_session_id(task))
                receiver = getattr(client, "receive_response", None)
                if not callable(receiver):
                    receiver = client.receive_messages
                async for message in receiver():
                    await stream.consume(message)
            except BaseException:
                # Evict the client on any non-normal exit — including
                # cancellation (CancelledError is a BaseException) — so a
                # poisoned process is never handed to the next run(). The run
                # lock is already held, so close under the client lock only and
                # never let cleanup mask the original error.
                close_exc = await finish_vendor_cleanup(self._close_client())
                self._cleanup_quarantine.track(close_exc)
                if close_exc is not None:
                    logger.warning(
                        "failed to close Claude client after run failure: %s", close_exc
                    )
                raise
            return process_reused

    async def _persistent_client(
        self,
        client_cls: Any,
        options: Any,
        *,
        key: tuple[Any, ...],
    ) -> tuple[Any, bool]:
        async with self._client_lock:
            if self._client is not None and self._client_key == key:
                self._sdk_process_reuse_count += 1
                return self._client, True
            if self._client is not None:
                await self._close_client_locked()
            client = client_cls(options)
            try:
                connect = getattr(client, "connect", None)
                if callable(connect):
                    await connect()
                else:
                    enter = getattr(client, "__aenter__", None)
                    if callable(enter):
                        client = await enter()
            except BaseException:
                close_exc = await finish_vendor_cleanup(
                    close_vendor_resource(client, try_disconnect=True)
                )
                self._cleanup_quarantine.track(close_exc)
                if close_exc is not None:
                    logger.warning(
                        "failed to close Claude client after startup failure: %s", close_exc
                    )
                raise
            self._client = client
            self._client_key = key
            self._sdk_process_start_count += 1
            return client, False

    async def _close_client_locked(self) -> None:
        client = self._client
        self._client = None
        self._client_key = None
        if client is None:
            return
        await close_vendor_resource(client, try_disconnect=True)

    def _process_reuse_metadata(self, reused: bool) -> dict[str, Any]:
        scope = "conversation" if _has_conversation_id(self._client_key) else "shared"
        return {
            "sdk_process_reuse_enabled": self._reuse_process,
            "sdk_process_reused": reused,
            "sdk_process_start_count": self._sdk_process_start_count,
            "sdk_process_reuse_count": self._sdk_process_reuse_count,
            "sdk_process_reuse_scope": scope,
        }

    def _build_options(
        self, task: AgentTask, model: str | None, options_cls: Any
    ) -> tuple[Any, list[str]]:
        metadata = task.metadata
        kwargs: dict[str, Any] = {
            "allowed_tools": list(task.permissions.allowed_tools),
            "disallowed_tools": list(task.permissions.disallowed_tools),
            "permission_mode": _effective_permission_mode(task.permissions),
        }
        if model is not None:
            kwargs["model"] = model
        if task.system:
            kwargs["system_prompt"] = task.system
        if self._env is not None:
            kwargs["env"] = dict(self._env)
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
        effort = task.reasoning_effort or metadata_str(metadata, "reasoning_effort")
        if effort:
            # claude-agent-sdk >= 0.2.87 accepts effort=. On older SDKs the drop is
            # recorded, not fatal: effort is a quality preference, unlike the
            # security/spend kwargs enforced above.
            kwargs["effort"] = effort
        output_schema = output_schema_from(task.output_schema, metadata)
        if output_schema is not None:
            kwargs["output_format"] = {"type": "json_schema", "schema": dict(output_schema)}
        setting_sources = metadata.get("setting_sources")
        if isinstance(setting_sources, list):
            kwargs["setting_sources"] = [str(item) for item in setting_sources]
        # Security posture must fail closed under vendor drift: the permission
        # mode always, and the tool filters whenever the task requested any.
        required = {"permission_mode": "permissions"}
        if task.permissions.allowed_tools:
            required["allowed_tools"] = "permissions"
        if task.permissions.disallowed_tools:
            required["disallowed_tools"] = "permissions"
        if task.budget_usd is not None:
            required["max_budget_usd"] = "budget_usd"
        supported, dropped = filter_supported_kwargs(
            options_cls, kwargs, required=required, kind=self.kind
        )
        return options_cls(**supported), dropped

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
        # tool_use_id -> result preview, so result.tool_calls carry a capped raw
        # preview of each tool result (previously result audits had none). Only
        # the RESULT carries content; the sanitized tool_completed events expose
        # just result_preview_length, never the preview text itself.
        self.tool_previews: dict[str, str] = {}

    async def consume(self, message: Any) -> None:
        self.messages.append(message)
        kind = self._runtime.kind
        task = self._task
        message_type = _message_type(message)
        if message_type in {"AssistantMessage", "assistant"}:
            for block in _iter_blocks(message):
                block_type = _message_type(block)
                if block_type in {"TextBlock", "text"}:
                    text = str(field_value(block, "text", ""))
                    if text:
                        self.emitted_delta = True
                        await safe_emit(task, output_delta_event(task, kind, text=text))
                elif block_type in {"ToolUseBlock", "tool_use"}:
                    name = str(field_value(block, "name", "tool"))
                    block_id = optional_str(field_value(block, "id"))
                    if block_id is not None:
                        self._tool_names[block_id] = name
                    raw_input = field_value(block, "input", {})
                    arguments = raw_input if isinstance(raw_input, Mapping) else {}
                    await safe_emit(
                        task,
                        tool_requested_event(task, kind, tool_name=name, arguments=arguments),
                    )
        elif message_type in {"UserMessage", "user"}:
            for block in _iter_blocks(message):
                if _message_type(block) not in {"ToolResultBlock", "tool_result"}:
                    continue
                tool_use_id = optional_str(field_value(block, "tool_use_id"))
                status = "error" if field_value(block, "is_error", False) else "ok"
                preview = str(field_value(block, "content", ""))[:256]
                if tool_use_id is not None:
                    self.tool_results[tool_use_id] = status
                    self.tool_previews[tool_use_id] = preview
                audit = ToolCallAudit(
                    tool_name=self._tool_names.get(tool_use_id or "", "tool"),
                    result_preview=preview,
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
    model: str | None,
    model_source: str,
    dropped_options: list[str],
    tool_results: Mapping[str, str],
    tool_previews: Mapping[str, str],
    permission_mode: str,
    process_metadata: Mapping[str, Any] | None = None,
) -> AgentResult:
    content_parts: list[str] = []
    tool_calls: list[ToolCallAudit] = []
    tool_use_ids: list[str | None] = []
    usage = Usage()
    cost_usd: float | None = None
    session_id = _conversation_id(task)
    rounds = 0
    error: str | None = None
    finish_reason = "done"
    structured_output: Any = STRUCTURED_OUTPUT_MISSING

    for message in messages:
        message_type = _message_type(message)
        if message_type in {"AssistantMessage", "assistant"}:
            text, tools, ids = _assistant_content(message)
            content_parts.extend(text)
            tool_calls.extend(tools)
            tool_use_ids.extend(ids)
            session_id = optional_str(field_value(message, "session_id")) or session_id
            usage = _usage_from(field_value(message, "usage"), current=usage)
            message_error = field_value(message, "error")
            if message_error:
                error = str(message_error)
                finish_reason = "failed"
        elif message_type in {"ResultMessage", "result"}:
            result_text = field_value(message, "result")
            if result_text and not content_parts:
                content_parts.append(str(result_text))
            structured_output = field_value(message, "structured_output", structured_output)
            reported_cost = field_value(message, "total_cost_usd")
            if reported_cost is not None:
                try:
                    candidate_cost = float(reported_cost)
                except (TypeError, ValueError):
                    pass
                else:
                    if candidate_cost >= 0 and isfinite(candidate_cost):
                        cost_usd = candidate_cost
            usage = _usage_from(field_value(message, "usage"), current=usage)
            rounds = int(field_value(message, "num_turns", rounds) or rounds)
            session_id = optional_str(field_value(message, "session_id")) or session_id
            if field_value(message, "is_error", False):
                finish_reason, error = _result_failure(message, result_text)

    output = "\n".join(part for part in content_parts if part).strip()
    schema = output_schema_from(task.output_schema, task.metadata)
    parsed_output: Any = None
    parsed_output_available = False
    if error is None and schema is not None:
        resolution = resolve_structured_output(
            schema,
            output,
            sdk_label="Claude Agent SDK",
            native=structured_output,
        )
        if resolution.error is not None:
            finish_reason = "failed"
            error = resolution.error
        else:
            parsed_output = resolution.value
            parsed_output_available = resolution.available
    if error is None and not output and not tool_calls and not parsed_output_available:
        finish_reason = "failed"
        error = empty_completion_error("Claude Agent SDK")
    usage = Usage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_creation_tokens=usage.cache_creation_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=cost_usd,
    )
    tool_calls = _apply_tool_results(tool_calls, tool_use_ids, tool_results, tool_previews)
    metadata: dict[str, Any] = {
        "model_source": model_source,
        "sdk": "claude_agent_sdk",
        "permission_mode": permission_mode,
        **dict(process_metadata or {}),
    }
    if model is not None:
        metadata["model"] = model
    if dropped_options:
        metadata["dropped_options"] = list(dropped_options)
    return AgentResult(
        output=output,
        finish_reason=finish_reason,
        error=error,
        parsed_output=parsed_output,
        parsed_output_available=parsed_output_available,
        usage=usage,
        tool_calls=tuple(tool_calls),
        session_id=session_id,
        rounds=rounds,
        metadata=metadata,
    )


def _result_failure(message: Any, result_text: Any) -> tuple[str, str]:
    subtype = str(field_value(message, "subtype", "") or "")
    finish_reason = "max_turns" if subtype == "error_max_turns" else "failed"
    errors = field_value(message, "errors", ()) or ()
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
    tool_previews: Mapping[str, str],
) -> list[ToolCallAudit]:
    if not tool_results and not tool_previews:
        return tool_calls
    updated: list[ToolCallAudit] = []
    for audit, tool_use_id in zip(tool_calls, tool_use_ids, strict=True):
        key = tool_use_id or ""
        status = tool_results.get(key)
        preview = tool_previews.get(key, audit.result_preview)
        new_status = status if status is not None else audit.status
        if new_status != audit.status or preview != audit.result_preview:
            updated.append(
                ToolCallAudit(
                    tool_name=audit.tool_name,
                    arguments=audit.arguments,
                    result_preview=preview,
                    status=new_status,
                    duration_ms=audit.duration_ms,
                )
            )
        else:
            updated.append(audit)
    return updated


def _permission_mode(mode: PermissionMode) -> str:
    # Claude's modes ordered by permissiveness: plan < default < bypassPermissions.
    # The portable ladder must stay monotonic: CAUTIOUS must never be looser than
    # DEFAULT. CAUTIOUS previously mapped to "acceptEdits" (auto-approves edits and
    # in-cwd deletes), which was strictly looser than DEFAULT's "default" — a
    # security footgun. Claude has no distinct cautious-execution tier, so CAUTIOUS
    # and DEFAULT both map to "default" (no auto-approval).
    if mode is PermissionMode.STRICT:
        return "plan"
    if mode is PermissionMode.PERMISSIVE:
        return "bypassPermissions"
    return "default"


def _effective_permission_mode(permissions: PermissionProfile) -> str:
    """Vendor permission_mode after applying the filesystem constraint.

    READ_ONLY is a hard constraint, so it forces "plan" (Claude's read/analyze,
    no-write posture) regardless of mode. Otherwise the mode mapping applies.
    Previously ``permissions.filesystem`` was ignored entirely, so a READ_ONLY
    task ran read-write on Claude while Codex/Antigravity honored it.
    """

    if permissions.filesystem is FilesystemAccess.READ_ONLY:
        return "plan"
    return _permission_mode(permissions.mode)


def _client_session_id(task: AgentTask) -> str:
    if task.resume_from is not None:
        return task.resume_from.session_id
    if task.session_id:
        return task.session_id
    return task.task_id


def _conversation_id(task: AgentTask) -> str | None:
    """Explicit conversation identity for reuse keying (no task_id fallback)."""

    if task.resume_from is not None:
        return task.resume_from.session_id
    return task.session_id


def _has_conversation_id(key: tuple[Any, ...] | None) -> bool:
    return bool(key and len(key) > 1 and key[1])


def _message_type(message: Any) -> str:
    if isinstance(message, Mapping):
        return str(message.get("type") or "")
    return type(message).__name__


def _iter_blocks(message: Any) -> Iterable[Any]:
    content = field_value(message, "content", ())
    if isinstance(content, str) or not isinstance(content, Iterable):
        return ()
    return content


def _assistant_content(
    message: Any,
) -> tuple[list[str], list[ToolCallAudit], list[str | None]]:
    content = field_value(message, "content", ())
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
            text_parts.append(str(field_value(block, "text", "")))
        elif block_type in {"ToolUseBlock", "tool_use"}:
            name = str(field_value(block, "name", "tool"))
            raw_input = field_value(block, "input", {})
            arguments = raw_input if isinstance(raw_input, Mapping) else {}
            tool_calls.append(ToolCallAudit(tool_name=name, arguments=arguments))
            tool_use_ids.append(optional_str(field_value(block, "id")))
    return text_parts, tool_calls, tool_use_ids


def _usage_from(value: Any, *, current: Usage) -> Usage:
    if not isinstance(value, Mapping):
        return current
    input_tokens = optional_int(value.get("input_tokens"))
    output_tokens = optional_int(value.get("output_tokens"))
    cache_creation = optional_int(value.get("cache_creation_input_tokens"))
    cache_read = optional_int(value.get("cache_read_input_tokens"))
    input_tokens = current.input_tokens if input_tokens is None else input_tokens
    output_tokens = current.output_tokens if output_tokens is None else output_tokens
    cache_creation = current.cache_creation_tokens if cache_creation is None else cache_creation
    cache_read = current.cache_read_tokens if cache_read is None else cache_read
    components = (input_tokens, output_tokens, cache_creation, cache_read)
    total = (
        sum(component for component in components if component is not None)
        if all(component is not None for component in components)
        else None
    )
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        total_tokens=total,
        cost_usd=current.cost_usd,
    )


def _claude_auth_metadata(runtime_env: Mapping[str, str] | None) -> dict[str, Any]:
    env = runtime_env or os.environ
    if _env_truthy(env, "CLAUDE_CODE_USE_BEDROCK"):
        metadata: dict[str, Any] = {
            "auth_source": "amazon-bedrock",
            "credential_chain": "aws-sdk",
        }
        region = _env_first(env, "AWS_REGION", "AWS_DEFAULT_REGION")
        if region:
            metadata["region"] = region
        if _env_first(env, "AWS_PROFILE"):
            metadata["aws_profile_configured"] = True
        if _env_first(env, "AWS_BEARER_TOKEN_BEDROCK"):
            metadata["bedrock_api_key_configured"] = True
        return metadata
    if _env_truthy(env, "CLAUDE_CODE_USE_VERTEX"):
        metadata = {
            "auth_source": "google-vertex-ai",
            "credential_chain": "google-application-default-credentials",
        }
        project = _env_first(env, "ANTHROPIC_VERTEX_PROJECT_ID", "GOOGLE_CLOUD_PROJECT")
        if project:
            metadata["project_configured"] = True
        return metadata
    if _env_truthy(env, "CLAUDE_CODE_USE_ANTHROPIC_AWS"):
        return {
            "auth_source": "claude-platform-aws",
            "workspace_configured": bool(_env_first(env, "ANTHROPIC_AWS_WORKSPACE_ID")),
        }
    if _env_truthy(env, "CLAUDE_CODE_USE_FOUNDRY"):
        return {"auth_source": "azure-ai-foundry"}
    if _env_first(env, "ANTHROPIC_API_KEY"):
        return {"auth_source": "anthropic-api-key"}
    if _env_first(env, "CLAUDE_CODE_OAUTH_TOKEN"):
        return {"auth_source": "claude-code-oauth-token"}
    return {"auth_source": "provider-owned-local"}


def _env_truthy(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name)
    return value is not None and value.lower() not in {"", "0", "false", "no"}


def _env_first(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = env.get(name)
        if value:
            return value
    return None
