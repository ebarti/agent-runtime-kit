"""OpenAI Codex SDK runtime adapter."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from typing import Any

from agent_runtime_kit._errors import AgentRuntimeUnavailableError, UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    PermissionMode,
    RuntimeAvailability,
    ToolCallAudit,
    Usage,
)
from agent_runtime_kit.adapters._common import (
    close_vendor_resource,
    empty_completion_error,
    ensure_supported_model,
    field_value,
    metadata_str,
    optional_int,
    output_schema_from,
    package_availability,
    parse_json_output,
    reject_unsupported_inputs,
    structured_output_unsatisfied_error,
)
from agent_runtime_kit.events import (
    output_delta_event,
    safe_emit,
    task_completed_event,
    task_failed_event,
    task_started_event,
)

# Vendor ``ThreadItem`` discriminator values that carry a tool/command invocation.
_COMMAND_ITEM = "commandExecution"
_MCP_ITEM = "mcpToolCall"
_DYNAMIC_ITEM = "dynamicToolCall"
_WEB_SEARCH_ITEM = "webSearch"
# Vendor status values (camelCase) that mean the invocation did not succeed.
_TOOL_ERROR_STATUSES = frozenset({"failed", "declined"})

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
        cancellation=False,
    )

    def __init__(
        self,
        *,
        default_model: str = "gpt-5.5",
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
        self._supported_models = supported_models
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

    async def __aenter__(self) -> CodexAgentRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()

    def availability(self) -> RuntimeAvailability:
        """Report OpenAI Codex SDK package availability."""

        auth_metadata = _codex_auth_metadata(self._config_overrides, self._env)
        if self._codex_cls is not None:
            return RuntimeAvailability.ok(
                self.kind,
                package="openai-codex",
                metadata=auth_metadata,
            )
        package = package_availability(
            self.kind,
            module_name="openai_codex",
            package_name="openai-codex",
        )
        if not package.available:
            return package
        return RuntimeAvailability.ok(
            self.kind,
            package="openai-codex",
            version=package.version,
            metadata={**dict(package.metadata), **auth_metadata},
        )

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task with the Codex SDK."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            if task.mcp_servers:
                raise UnsupportedTaskInputError(
                    self.kind,
                    "mcp_servers",
                    "openai_codex does not expose per-task MCP server configuration",
                )
            reject_unsupported_inputs(self.kind, task, budget=True, network=True, tool_filters=True)
            model = self._model(task)
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            codex_cls, config_cls, sandbox_cls, approval_mode_cls = self._load_sdk()
            result = await self._run_codex(
                task,
                model=model,
                codex_cls=codex_cls,
                config_cls=config_cls,
                sandbox_cls=sandbox_cls,
                approval_mode_cls=approval_mode_cls,
            )
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

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

    async def cancel(self, task_id: str) -> None:
        """Codex SDK cancellation is not exposed through this portable adapter yet."""

        del task_id

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

    async def _run_codex(
        self,
        task: AgentTask,
        *,
        model: str,
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
        process_reused = False
        try:
            if self._reuse_process:
                async with self._codex_run_lock:
                    key = _codex_client_key(
                        config_kwargs,
                        model=model,
                        permissions=task.permissions,
                        sandbox_cls=sandbox_cls,
                        approval_mode_cls=approval_mode_cls,
                    )
                    codex, process_reused = await self._persistent_codex_client(
                        codex_cls,
                        config_cls,
                        config_kwargs,
                        key=key,
                    )
                    thread, raw_result = await self._invoke_codex(
                        codex,
                        task,
                        model=model,
                        cwd=cwd,
                        sandbox_cls=sandbox_cls,
                        approval_mode_cls=approval_mode_cls,
                    )
            else:
                config = config_cls(**config_kwargs)
                async with codex_cls(config=config) as codex:
                    thread, raw_result = await self._invoke_codex(
                        codex,
                        task,
                        model=model,
                        cwd=cwd,
                        sandbox_cls=sandbox_cls,
                        approval_mode_cls=approval_mode_cls,
                    )
        except BaseException:
            # Evict the reused app-server on any non-normal exit — including
            # cancellation (CancelledError is a BaseException) — so the next
            # run() never reuses a poisoned process. The reuse branch holds the
            # run lock, so close under the client lock only and never let
            # cleanup mask the original error.
            if self._reuse_process:
                try:
                    await self._close_codex_client()
                except Exception as close_exc:
                    logger.warning(
                        "failed to close Codex app-server after run failure: %s", close_exc
                    )
            raise
        return _translate_run_result(
            task,
            raw_result,
            model=model,
            session_id=_thread_id(thread) or _task_session_id(task),
            process_metadata=(
                self._process_reuse_metadata(process_reused) if self._reuse_process else None
            ),
        )

    async def _invoke_codex(
        self,
        codex: Any,
        task: AgentTask,
        *,
        model: str,
        cwd: str | None,
        sandbox_cls: Any,
        approval_mode_cls: Any,
    ) -> tuple[Any, Any]:
        thread = await self._start_or_resume_thread(
            codex,
            task,
            model=model,
            cwd=cwd,
            sandbox_cls=sandbox_cls,
            approval_mode_cls=approval_mode_cls,
        )
        run_kwargs = {
            "cwd": cwd,
            "model": model,
            "approval_mode": _approval_mode(task.permissions.mode, approval_mode_cls),
            "sandbox": _sandbox_mode(task.permissions.filesystem, sandbox_cls),
        }
        schema = output_schema_from(task.output_schema, task.metadata)
        if schema is not None:
            run_kwargs["output_schema"] = dict(schema)
        effort = metadata_str(task.metadata, "reasoning_effort")
        if effort:
            run_kwargs["effort"] = effort
        raw_result = await thread.run(task.goal, **run_kwargs)
        return thread, raw_result

    async def _persistent_codex_client(
        self,
        codex_cls: Any,
        config_cls: Any,
        config_kwargs: Mapping[str, Any],
        *,
        key: tuple[Any, ...],
    ) -> tuple[Any, bool]:
        async with self._codex_client_lock:
            if self._codex_client is not None and self._codex_client_key == key:
                self._sdk_process_reuse_count += 1
                return self._codex_client, True
            if self._codex_client is not None:
                await self._close_codex_client_locked()
            context = codex_cls(config=config_cls(**dict(config_kwargs)))
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
        model: str,
        cwd: str | None,
        sandbox_cls: Any,
        approval_mode_cls: Any,
    ) -> Any:
        kwargs = {
            "cwd": cwd,
            "developer_instructions": task.system,
            "model": model,
            "approval_mode": _approval_mode(task.permissions.mode, approval_mode_cls),
            "sandbox": _sandbox_mode(task.permissions.filesystem, sandbox_cls),
        }
        thread_id = task.resume_from.session_id if task.resume_from is not None else task.session_id
        if thread_id:
            return await codex.thread_resume(thread_id, **kwargs)
        return await codex.thread_start(**kwargs)

    def _model(self, task: AgentTask) -> str:
        return metadata_str(task.metadata, "model") or self._default_model


def _translate_run_result(
    task: AgentTask,
    raw_result: Any,
    *,
    model: str,
    session_id: str | None,
    process_metadata: Mapping[str, Any] | None = None,
) -> AgentResult:
    output = str(field_value(raw_result, "final_response", "") or "")
    usage = _codex_usage(field_value(raw_result, "usage"))
    tool_calls = tuple(_tool_audits(field_value(raw_result, "items", ()) or ()))
    metadata = {"model": model, "sdk": "openai_codex", **dict(process_metadata or {})}
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

    # status is "completed" or missing (dict-based fakes): treat as success and keep
    # the empty-output and schema-parse-failure branches.
    schema = output_schema_from(task.output_schema, task.metadata)
    parsed = parse_json_output(output) if schema is not None else None
    if schema is not None and parsed is None:
        return AgentResult(
            output=output,
            finish_reason="failed",
            error=structured_output_unsatisfied_error("Codex SDK"),
            usage=usage,
            tool_calls=tool_calls,
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )
    if not output and not tool_calls and parsed is None:
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
            duration_ms=optional_int(field_value(item, "duration_ms")),
        )
    if item_type in {_MCP_ITEM, _DYNAMIC_ITEM}:
        return ToolCallAudit(
            tool_name=str(field_value(item, "tool", "tool") or "tool"),
            arguments=_tool_arguments(field_value(item, "arguments")),
            result_preview=str(field_value(item, "result", "") or "")[:256],
            status=_tool_status(item),
            duration_ms=optional_int(field_value(item, "duration_ms")),
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
    input_tokens = optional_int(field_value(breakdown, "input_tokens"))
    output_tokens = optional_int(field_value(breakdown, "output_tokens"))
    cached = optional_int(field_value(breakdown, "cached_input_tokens"))
    total_tokens = optional_int(field_value(breakdown, "total_tokens"))
    # OpenAI reports cached input inside input_tokens, so never add cached on top.
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached,
        total_tokens=total_tokens or input_tokens + output_tokens,
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
    model: str,
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

