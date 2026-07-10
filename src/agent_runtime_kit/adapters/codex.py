"""OpenAI Codex SDK runtime adapter."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
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
    ReadinessStatus,
    RuntimeAvailability,
    RuntimeReadiness,
    TaskSupportReport,
    ToolCallAudit,
    Usage,
)
from agent_runtime_kit.adapters._common import (
    close_vendor_resource,
    empty_completion_error,
    field_value,
    filter_supported_kwargs,
    metadata_str,
    model_support_issue,
    optional_int,
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

# Vendor ``ThreadItem`` discriminator values that carry a tool/command invocation.
_COMMAND_ITEM = "commandExecution"
_MCP_ITEM = "mcpToolCall"
_DYNAMIC_ITEM = "dynamicToolCall"
_WEB_SEARCH_ITEM = "webSearch"
# Vendor status values (camelCase) that mean the invocation did not succeed.
_TOOL_ERROR_STATUSES = frozenset({"failed", "declined"})
# Kwargs that carry the task's requested security posture. Vendor drift on these
# must fail closed rather than silently run under the SDK's default sandbox.
_SECURITY_KWARGS = ("approval_mode", "sandbox")

logger = logging.getLogger(__name__)


class CodexAgentRuntime:
    """Run tasks through the official ``openai_codex`` Python SDK."""

    kind = AgentRuntimeKind.CODEX_AGENT_SDK
    capabilities = AgentCapabilities(
        mcp_support=False,
        working_directory=True,
        session_resume=True,
        structured_output=True,
        streaming=False,
        tool_audit=True,
        cancellation=True,
        reasoning_effort=True,
    )

    def __init__(
        self,
        *,
        default_model: str | None = None,
        supported_models: tuple[str, ...] | None = None,
        config_overrides: tuple[str, ...] = ("features.plugins=false",),
        env: Mapping[str, str] | None = None,
        codex_cls: Any | None = None,
        config_cls: Any | None = None,
        sandbox_cls: Any | None = None,
        approval_mode_cls: Any | None = None,
        reuse_process: bool = False,
    ) -> None:
        self._default_model = default_model
        self._supported_models = validate_model_configuration(
            default_model, supported_models
        )
        # Plugins are disabled by default so headless runs are deterministic and do
        # not pick up host-local Codex plugin configuration. Override to opt in.
        self._config_overrides = config_overrides
        self._env = dict(env) if env is not None else None
        self._codex_cls = codex_cls
        self._config_cls = config_cls
        self._sandbox_cls = sandbox_cls
        self._approval_mode_cls = approval_mode_cls
        self._reuse_process = reuse_process
        self._codex_context: Any | None = None
        self._codex_client: Any | None = None
        self._codex_client_key: tuple[Any, ...] | None = None
        self._sdk_process_start_count = 0
        self._sdk_process_reuse_count = 0
        self._codex_client_lock = asyncio.Lock()
        self._codex_run_lock = asyncio.Lock()
        self._task_controller = RuntimeTaskController(self.kind)

    async def __aenter__(self) -> CodexAgentRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()

    def availability(self) -> RuntimeAvailability:
        """Report Codex package presence without starting app-server."""

        if self._codex_cls is not None:
            return RuntimeAvailability.ok(self.kind, package="openai-codex")
        return package_availability(self.kind)

    async def check_readiness(self) -> RuntimeReadiness:
        """Ask the supported SDK account API whether Codex is authenticated."""

        availability = self.availability()
        if not availability.available:
            return RuntimeReadiness.from_availability(
                availability,
                status=ReadinessStatus.NOT_READY,
            )
        auth_metadata = _codex_auth_metadata(self._config_overrides, self._env)
        try:
            account_response = await self._read_account()
        except Exception as exc:
            return RuntimeReadiness.indeterminate(
                self.kind,
                reason=AvailabilityReason.SETUP_FAILED,
                message="The Codex account probe failed before authentication was known.",
                package=availability.package,
                version=availability.version,
                metadata={
                    **auth_metadata,
                    "failure": "account-probe",
                    "error_type": type(exc).__name__,
                },
            )
        account = field_value(account_response, "account")
        if account is None:
            return RuntimeReadiness.not_ready(
                self.kind,
                reason=AvailabilityReason.MISSING_CREDENTIALS,
                message="The Codex SDK reported no authenticated account.",
                package=availability.package,
                version=availability.version,
                metadata=auth_metadata,
            )
        account_root = field_value(account, "root", account)
        account_type = field_value(account_root, "type")
        safe_account_types = {
            "apiKey": "api-key",
            "chatgpt": "chatgpt",
            "amazonBedrock": "amazon-bedrock",
        }
        if account_type in safe_account_types:
            auth_metadata["account_type"] = safe_account_types[account_type]
        return RuntimeReadiness.ready_to_attempt(
            self.kind,
            message="The Codex SDK reported an authenticated account.",
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
        """Execute one deadline- and cancellation-controlled Codex task."""

        return await self._task_controller.run(task, lambda: self._run_task(task))

    async def _run_task(self, task: AgentTask) -> AgentResult:
        """Execute one task with the Codex SDK."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            require_task_support(self.validate_task(task))
            selection = select_model(task, self._default_model)
            model = selection.value
            codex_cls, config_cls, sandbox_cls, approval_mode_cls = self._load_sdk()
            result = await self._run_codex(
                task,
                model=model,
                model_source=selection.source,
                codex_cls=codex_cls,
                config_cls=config_cls,
                sandbox_cls=sandbox_cls,
                approval_mode_cls=approval_mode_cls,
            )
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

        # Codex is non-streaming, so tool calls are parsed from the final TurnResult.
        # Emit them as events anyway so an observability sink sees the same tool
        # activity here as it does from the streaming Claude/Antigravity adapters,
        # instead of only in result.tool_calls.
        for audit in result.tool_calls:
            await safe_emit(
                task,
                tool_requested_event(
                    task, self.kind, tool_name=audit.tool_name, arguments=audit.arguments
                ),
            )
            await safe_emit(task, tool_completed_event(task, self.kind, audit))

        if result.output:
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
        """Close any reusable Codex app-server process owned by this runtime.

        Acquires the run lock first so an external ``aclose()`` waits for an
        in-flight ``run()`` instead of closing the process mid-turn.
        """

        async with self._codex_run_lock:
            await self._close_codex_client()

    async def _close_codex_client(self) -> None:
        """Close the app-server holding only the client lock (run lock assumed free)."""

        async with self._codex_client_lock:
            await self._close_codex_client_locked()

    def _load_sdk(self) -> tuple[Any, Any, Any, Any]:
        if self._codex_cls is not None and self._config_cls is not None:
            return (
                self._codex_cls,
                self._config_cls,
                self._sandbox_cls,
                self._approval_mode_cls,
            )
        try:  # pragma: no cover - real SDK import, exercised via injected fakes in tests
            from openai_codex import (
                ApprovalMode,
                AsyncCodex,
                CodexConfig,
                Sandbox,
            )
        except ImportError as exc:  # pragma: no cover
            raise AgentRuntimeUnavailableError(
                self.kind,
                "openai-codex is not installed. Install agent-runtime-kit[codex].",
            ) from exc
        return (  # pragma: no cover
            self._codex_cls or AsyncCodex,
            self._config_cls or CodexConfig,
            self._sandbox_cls or Sandbox,
            self._approval_mode_cls or ApprovalMode,
        )

    async def _read_account(self) -> Any:
        codex_cls, config_cls, _, _ = self._load_sdk()
        config_kwargs: dict[str, Any] = {"config_overrides": self._config_overrides}
        if self._env is not None:
            config_kwargs["env"] = dict(self._env)
        supported, _ = filter_supported_kwargs(config_cls, config_kwargs)
        context = codex_cls(config=config_cls(**supported))
        client = context
        try:
            enter = getattr(context, "__aenter__", None)
            if callable(enter):
                client = await enter()
            account = getattr(client, "account", None)
            if not callable(account):
                raise TypeError("installed Codex SDK does not expose AsyncCodex.account")
            return await account(refresh_token=False)
        finally:
            await close_vendor_resource(context, fallback=client)

    async def _run_codex(
        self,
        task: AgentTask,
        *,
        model: str | None,
        model_source: str,
        codex_cls: Any,
        config_cls: Any,
        sandbox_cls: Any,
        approval_mode_cls: Any,
    ) -> AgentResult:
        cwd = str(task.working_directory) if task.working_directory else None
        config_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "config_overrides": self._config_overrides,
        }
        if self._env is not None:
            config_kwargs["env"] = dict(self._env)
        # Tolerate vendor option drift like the Claude adapter: drop kwargs the
        # installed SDK no longer accepts (instead of crashing with TypeError) and
        # record them so the omission stays visible in AgentResult.metadata.
        config_supported, dropped = filter_supported_kwargs(config_cls, config_kwargs)
        process_reused = False
        if self._reuse_process:
            async with self._codex_run_lock:
                try:
                    key = _codex_client_key(
                        config_kwargs,
                        model=model,
                        permissions=task.permissions,
                        sandbox_cls=sandbox_cls,
                        approval_mode_cls=approval_mode_cls,
                    )
                    codex, process_reused = await self._persistent_codex_client(
                        codex_cls,
                        config_cls(**config_supported),
                        key=key,
                    )
                    thread, raw_result, invoke_dropped = await self._invoke_codex(
                        codex,
                        task,
                        model=model,
                        cwd=cwd,
                        sandbox_cls=sandbox_cls,
                        approval_mode_cls=approval_mode_cls,
                    )
                except BaseException:
                    # Evict the reused app-server on any non-normal exit —
                    # including cancellation (CancelledError is a BaseException)
                    # — so the next run() never reuses a poisoned process. Runs
                    # inside the run lock so a queued run cannot observe the
                    # doomed client between failure and eviction; close under
                    # the client lock only and never mask the original error.
                    try:
                        await self._close_codex_client()
                    except Exception as close_exc:
                        logger.warning(
                            "failed to close Codex app-server after run failure: %s", close_exc
                        )
                    raise
        else:
            # Per-call isolation: the context manager owns the process teardown.
            async with codex_cls(config=config_cls(**config_supported)) as codex:
                thread, raw_result, invoke_dropped = await self._invoke_codex(
                    codex,
                    task,
                    model=model,
                    cwd=cwd,
                    sandbox_cls=sandbox_cls,
                    approval_mode_cls=approval_mode_cls,
                )
        for key_name in invoke_dropped:
            if key_name not in dropped:
                dropped.append(key_name)
        return _translate_run_result(
            task,
            raw_result,
            model=model,
            model_source=model_source,
            session_id=_thread_id(thread) or _task_session_id(task),
            dropped_options=dropped,
            process_metadata=(
                self._process_reuse_metadata(process_reused) if self._reuse_process else None
            ),
        )

    async def _invoke_codex(
        self,
        codex: Any,
        task: AgentTask,
        *,
        model: str | None,
        cwd: str | None,
        sandbox_cls: Any,
        approval_mode_cls: Any,
    ) -> tuple[Any, Any, list[str]]:
        thread, dropped = await self._start_or_resume_thread(
            codex,
            task,
            model=model,
            cwd=cwd,
            sandbox_cls=sandbox_cls,
            approval_mode_cls=approval_mode_cls,
        )
        run_kwargs: dict[str, Any] = {
            "cwd": cwd,
            "approval_mode": _approval_mode(task.permissions.mode, approval_mode_cls),
            "sandbox": _sandbox_mode(task.permissions.filesystem, sandbox_cls),
        }
        if model is not None:
            run_kwargs["model"] = model
        schema = output_schema_from(task.output_schema, task.metadata)
        if schema is not None:
            run_kwargs["output_schema"] = dict(schema)
        effort = task.reasoning_effort or metadata_str(task.metadata, "reasoning_effort")
        if effort:
            run_kwargs["effort"] = effort
        run_supported, run_dropped = filter_supported_kwargs(
            thread.run, run_kwargs, required=_SECURITY_KWARGS, kind=self.kind
        )
        raw_result = await thread.run(task.goal, **run_supported)
        return thread, raw_result, dropped + [k for k in run_dropped if k not in dropped]

    async def _persistent_codex_client(
        self,
        codex_cls: Any,
        config: Any,
        *,
        key: tuple[Any, ...],
    ) -> tuple[Any, bool]:
        async with self._codex_client_lock:
            if self._codex_client is not None and self._codex_client_key == key:
                self._sdk_process_reuse_count += 1
                return self._codex_client, True
            if self._codex_client is not None:
                await self._close_codex_client_locked()
            context = codex_cls(config=config)
            enter = getattr(context, "__aenter__", None)
            try:
                client = await enter() if callable(enter) else context
            except BaseException:
                try:
                    await close_vendor_resource(context)
                except Exception as close_exc:
                    logger.warning(
                        "failed to close Codex app-server after startup failure: %s", close_exc
                    )
                raise
            self._codex_context = context
            self._codex_client = client
            self._codex_client_key = key
            self._sdk_process_start_count += 1
            return client, False

    async def _close_codex_client_locked(self) -> None:
        context = self._codex_context
        client = self._codex_client
        self._codex_context = None
        self._codex_client = None
        self._codex_client_key = None
        await close_vendor_resource(context, fallback=client)

    def _process_reuse_metadata(self, reused: bool) -> dict[str, Any]:
        return {
            "sdk_process_reuse_enabled": self._reuse_process,
            "sdk_process_reused": reused,
            "sdk_process_start_count": self._sdk_process_start_count,
            "sdk_process_reuse_count": self._sdk_process_reuse_count,
        }

    async def _start_or_resume_thread(
        self,
        codex: Any,
        task: AgentTask,
        *,
        model: str | None,
        cwd: str | None,
        sandbox_cls: Any,
        approval_mode_cls: Any,
    ) -> tuple[Any, list[str]]:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "developer_instructions": task.system,
            "approval_mode": _approval_mode(task.permissions.mode, approval_mode_cls),
            "sandbox": _sandbox_mode(task.permissions.filesystem, sandbox_cls),
        }
        if model is not None:
            kwargs["model"] = model
        thread_id = task.resume_from.session_id if task.resume_from is not None else task.session_id
        if thread_id:
            supported, dropped = filter_supported_kwargs(
                codex.thread_resume, kwargs, required=_SECURITY_KWARGS, kind=self.kind
            )
            return await codex.thread_resume(thread_id, **supported), dropped
        supported, dropped = filter_supported_kwargs(
            codex.thread_start, kwargs, required=_SECURITY_KWARGS, kind=self.kind
        )
        return await codex.thread_start(**supported), dropped

def _translate_run_result(
    task: AgentTask,
    raw_result: Any,
    *,
    model: str | None,
    model_source: str,
    session_id: str | None,
    dropped_options: list[str] | None = None,
    process_metadata: Mapping[str, Any] | None = None,
) -> AgentResult:
    output = str(field_value(raw_result, "final_response", "") or "")
    usage = _codex_usage(field_value(raw_result, "usage"))
    tool_calls = tuple(_tool_audits(field_value(raw_result, "items", ()) or ()))
    metadata: dict[str, Any] = {
        "model_source": model_source,
        "sdk": "openai_codex",
        **dict(process_metadata or {}),
    }
    if model is not None:
        metadata["model"] = model
    if dropped_options:
        metadata["dropped_options"] = list(dropped_options)
    status = _status_value(field_value(raw_result, "status"))

    if status == "failed":
        return AgentResult(
            output=output,
            finish_reason="failed",
            error=_turn_error(raw_result) or "Codex turn failed",
            usage=usage,
            tool_calls=tool_calls,
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )
    if status == "interrupted":
        return AgentResult(
            output=output,
            finish_reason="interrupted",
            error=_turn_error(raw_result) or "Codex turn interrupted",
            usage=usage,
            tool_calls=tool_calls,
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )
    if status not in (None, "", "completed"):
        # Fail closed on any other status: the SDK's non-terminal "inProgress" and
        # whatever statuses a future SDK adds must not read as success with partial
        # output (mirrors Antigravity, where unknown stop reasons map to failed).
        return AgentResult(
            output=output,
            finish_reason="failed",
            error=_turn_error(raw_result) or f"Codex turn ended with unexpected status {status!r}",
            usage=usage,
            tool_calls=tool_calls,
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )

    # status is "completed" or missing (dict-based fakes): treat as success and keep
    # the empty-output and schema-parse-failure branches.
    schema = output_schema_from(task.output_schema, task.metadata)
    parsed: Any = None
    parsed_available = False
    if schema is not None:
        resolution = resolve_structured_output(schema, output, sdk_label="Codex SDK")
        if resolution.error is None:
            parsed = resolution.value
            parsed_available = resolution.available
        else:
            return AgentResult(
                output=output,
                finish_reason="failed",
                error=resolution.error,
                usage=usage,
                tool_calls=tool_calls,
                session_id=session_id,
                rounds=1,
                metadata=metadata,
            )
    if not output and not tool_calls and not parsed_available:
        # Empty output alone is not a failure when the turn did real tool work;
        # only a completion with nothing usable is (matches Claude/Antigravity).
        return AgentResult(
            output="",
            finish_reason="failed",
            error=empty_completion_error("Codex SDK"),
            usage=usage,
            tool_calls=tool_calls,
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )
    return AgentResult(
        output=output,
        parsed_output=parsed,
        parsed_output_available=parsed_available,
        usage=usage,
        tool_calls=tool_calls,
        session_id=session_id,
        rounds=1,
        metadata=metadata,
    )


def _status_value(status: Any) -> str | None:
    if status is None:
        return None
    return str(getattr(status, "value", status) or "")


def _turn_error(raw_result: Any) -> str | None:
    error = field_value(raw_result, "error")
    if error is None:
        return None
    message = field_value(error, "message")
    return str(message) if message else None


def _tool_audits(items: Any) -> list[ToolCallAudit]:
    audits: list[ToolCallAudit] = []
    if isinstance(items, Mapping) or isinstance(items, (str, bytes)):
        return audits
    try:
        iterator = iter(items)
    except TypeError:
        return audits
    for item in iterator:
        audit = _tool_audit(item)
        if audit is not None:
            audits.append(audit)
    return audits


def _tool_audit(item: Any) -> ToolCallAudit | None:
    # TurnResult.items holds ThreadItem RootModel wrappers whose discriminated value
    # lives on ``.root``; unwrap it so duck-typed field access reaches the real item.
    if not isinstance(item, Mapping) and field_value(item, "type") is None:
        root = getattr(item, "root", None)
        if root is not None:
            item = root
    item_type = str(field_value(item, "type", "") or "")
    if item_type == _COMMAND_ITEM:
        return ToolCallAudit(
            tool_name="command",
            arguments={"command": str(field_value(item, "command", ""))},
            result_preview=str(field_value(item, "aggregated_output", "") or "")[:256],
            status=_tool_status(item),
            duration_ms=optional_int(field_value(item, "duration_ms")) or 0,
        )
    if item_type in {_MCP_ITEM, _DYNAMIC_ITEM}:
        return ToolCallAudit(
            tool_name=str(field_value(item, "tool", "tool") or "tool"),
            arguments=_tool_arguments(field_value(item, "arguments")),
            result_preview=str(field_value(item, "result", "") or "")[:256],
            status=_tool_status(item),
            duration_ms=optional_int(field_value(item, "duration_ms")) or 0,
        )
    if item_type == _WEB_SEARCH_ITEM:
        return ToolCallAudit(
            tool_name="web_search",
            arguments={"query": str(field_value(item, "query", ""))},
            status="ok",
        )
    return None


def _tool_status(item: Any) -> str:
    status = _status_value(field_value(item, "status"))
    if status in _TOOL_ERROR_STATUSES:
        return "error"
    if field_value(item, "error") is not None:
        return "error"
    return "ok"


def _tool_arguments(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _codex_usage(value: Any) -> Usage:
    # Prefer the per-turn breakdown ('last') over the thread-cumulative 'total':
    # on a resumed thread 'total' re-reports every prior turn's tokens, inflating
    # this turn's usage and cost accounting. Fall back to 'total' when 'last' is
    # absent (older SDKs / dict-based fakes).
    breakdown = field_value(value, "last")
    if breakdown is None:
        breakdown = field_value(value, "total")
    if breakdown is None and isinstance(value, Mapping):
        breakdown = value.get("total", value)
    if breakdown is None:
        return Usage()
    raw_input_tokens = optional_int(field_value(breakdown, "input_tokens"))
    output_tokens = optional_int(field_value(breakdown, "output_tokens"))
    cached = optional_int(field_value(breakdown, "cached_input_tokens"))
    total_tokens = optional_int(field_value(breakdown, "total_tokens"))
    # OpenAI reports cached input INSIDE input_tokens, while the Usage contract
    # (and the Antigravity adapter) excludes cache reads from input_tokens and
    # reports them separately. Subtract rather than double-count across the two
    # fields; the raw value still backs the vendor-style total fallback.
    input_tokens = (
        max(raw_input_tokens - cached, 0)
        if raw_input_tokens is not None and cached is not None
        else None
    )
    if total_tokens is None and raw_input_tokens is not None and output_tokens is not None:
        total_tokens = raw_input_tokens + output_tokens
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached,
        total_tokens=total_tokens,
    )


def _approval_mode(mode: PermissionMode, approval_mode_cls: Any) -> Any:
    # auto_review = sandbox escalations allowed and auto-adjudicated;
    # deny_all = never escalate. STRICT/CAUTIOUS lock down; DEFAULT/PERMISSIVE allow.
    deny = mode in {PermissionMode.STRICT, PermissionMode.CAUTIOUS}
    if approval_mode_cls is None:
        return "deny_all" if deny else "auto_review"
    if deny:
        return getattr(approval_mode_cls, "deny_all", "deny_all")
    return getattr(approval_mode_cls, "auto_review", "auto_review")


def _sandbox_mode(filesystem: FilesystemAccess, sandbox_cls: Any) -> Any:
    name = {
        FilesystemAccess.READ_ONLY: "read_only",
        FilesystemAccess.WORKSPACE_WRITE: "workspace_write",
        FilesystemAccess.FULL_ACCESS: "full_access",
    }[filesystem]
    if sandbox_cls is None:
        return name.replace("_", "-")
    return getattr(sandbox_cls, name, name.replace("_", "-"))


def _codex_client_key(
    config_kwargs: Mapping[str, Any],
    *,
    model: str | None,
    permissions: Any,
    sandbox_cls: Any,
    approval_mode_cls: Any,
) -> tuple[Any, ...]:
    env = config_kwargs.get("env")
    env_items = (
        tuple(sorted((str(key), str(value)) for key, value in env.items()))
        if isinstance(env, Mapping)
        else ()
    )
    overrides = config_kwargs.get("config_overrides")
    if isinstance(overrides, tuple):
        override_items = tuple(str(item) for item in overrides)
    elif isinstance(overrides, list):
        override_items = tuple(str(item) for item in overrides)
    elif overrides is None:
        override_items = ()
    else:
        override_items = (str(overrides),)
    return (
        str(config_kwargs.get("cwd") or ""),
        override_items,
        env_items,
        model,
        str(permissions.mode),
        str(permissions.filesystem),
        str(_approval_mode(permissions.mode, approval_mode_cls)),
        str(_sandbox_mode(permissions.filesystem, sandbox_cls)),
    )


def _task_session_id(task: AgentTask) -> str | None:
    """Conversation id the caller supplied, for result session_id fallback."""

    if task.resume_from is not None:
        return task.resume_from.session_id
    return task.session_id


def _thread_id(thread: Any) -> str | None:
    value = getattr(thread, "id", None)
    return str(value) if value else None


def _codex_auth_metadata(
    config_overrides: tuple[str, ...], runtime_env: Mapping[str, str] | None
) -> dict[str, Any]:
    if _uses_bedrock_provider(config_overrides):
        metadata: dict[str, Any] = {
            "auth_source": "amazon-bedrock",
            "credential_chain": "aws-sdk",
        }
        env = runtime_env or os.environ
        region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
        if region:
            metadata["region"] = region
        if env.get("AWS_PROFILE"):
            metadata["aws_profile_configured"] = True
        return metadata
    env = runtime_env or os.environ
    if env.get("CODEX_ACCESS_TOKEN"):
        return {"auth_source": "chatgpt-access-token"}
    if env.get("OPENAI_API_KEY"):
        return {"auth_source": "openai-api-key"}
    return {"auth_source": "provider-owned-local"}


def _uses_bedrock_provider(config_overrides: tuple[str, ...]) -> bool:
    for override in config_overrides:
        key, _, value = override.partition("=")
        normalized_key = key.strip().replace('"', "").replace("'", "")
        normalized_value = value.strip().strip('"').strip("'")
        if normalized_key == "model_provider" and normalized_value == "amazon-bedrock":
            return True
    return False
