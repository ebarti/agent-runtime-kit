"""OpenAI Codex SDK runtime adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    PermissionMode,
    RuntimeAvailability,
    Usage,
)
from agent_runtime_kit.adapters._common import (
    ensure_supported_model,
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
)


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
        codex_cls: Any | None = None,
        config_cls: Any | None = None,
        sandbox_cls: Any | None = None,
        approval_mode_cls: Any | None = None,
    ) -> None:
        self._default_model = default_model
        self._supported_models = supported_models
        self._codex_cls = codex_cls
        self._config_cls = config_cls
        self._sandbox_cls = sandbox_cls
        self._approval_mode_cls = approval_mode_cls

    def availability(self) -> RuntimeAvailability:
        """Report OpenAI Codex SDK package availability."""

        if self._codex_cls is not None:
            return RuntimeAvailability.ok(self.kind, package="openai-codex")
        return package_availability(
            self.kind,
            module_name="openai_codex",
            package_name="openai-codex",
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
            await safe_emit(task, task_failed_event(task, self.kind, error=result.error))
        else:
            await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> None:
        """Codex SDK cancellation is not exposed through this portable adapter yet."""

        del task_id

    def _load_sdk(self) -> tuple[Any, Any, Any, Any]:
        if self._codex_cls is not None and self._config_cls is not None:
            return (
                self._codex_cls,
                self._config_cls,
                self._sandbox_cls,
                self._approval_mode_cls,
            )
        try:
            from openai_codex import (  # type: ignore[import-not-found]
                ApprovalMode,
                AsyncCodex,
                CodexConfig,
                Sandbox,
            )
        except ImportError as exc:
            raise RuntimeError(
                "openai-codex is not installed. Install agent-runtime-kit[codex]."
            ) from exc
        return (
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
        config = config_cls(cwd=cwd, config_overrides=("features.plugins=false",))
        async with codex_cls(config=config) as codex:
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
        return _translate_run_result(task, raw_result, model=model, session_id=_thread_id(thread))

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
) -> AgentResult:
    output = str(_field(raw_result, "final_response", "") or "")
    usage = _codex_usage(_field(raw_result, "usage"))
    schema = output_schema_from(task.output_schema, task.metadata)
    parsed = parse_json_output(output) if schema is not None else None
    if schema is not None and parsed is None:
        return AgentResult(
            output=output,
            finish_reason="failed",
            error="Codex SDK returned output that did not satisfy output_schema",
            usage=usage,
            session_id=session_id,
            rounds=1,
            metadata={"model": model, "sdk": "openai_codex"},
        )
    if not output:
        return AgentResult(
            output="",
            finish_reason="failed",
            error="Codex SDK completed without final_response",
            usage=usage,
            session_id=session_id,
            metadata={"model": model, "sdk": "openai_codex"},
        )
    return AgentResult(
        output=output,
        parsed_output=parsed,
        usage=usage,
        session_id=session_id,
        rounds=1,
        metadata={"model": model, "sdk": "openai_codex"},
    )


def _codex_usage(value: Any) -> Usage:
    total = _field(value, "total")
    if total is None and isinstance(value, Mapping):
        total = value.get("total", value)
    input_tokens = _optional_int(_field(total, "input_tokens"))
    output_tokens = _optional_int(_field(total, "output_tokens"))
    cached = _optional_int(_field(total, "cached_input_tokens"))
    total_tokens = _optional_int(_field(total, "total_tokens"))
    return Usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached,
        total_tokens=total_tokens or input_tokens + output_tokens + cached,
    )


def _approval_mode(mode: PermissionMode, approval_mode_cls: Any) -> Any:
    if approval_mode_cls is None:
        return "auto_review" if mode is not PermissionMode.PERMISSIVE else "deny_all"
    if mode is PermissionMode.PERMISSIVE:
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


def _thread_id(thread: Any) -> str | None:
    value = getattr(thread, "id", None)
    return str(value) if value else None


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _optional_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
