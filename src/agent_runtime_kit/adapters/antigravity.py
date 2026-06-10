"""Google Antigravity SDK runtime adapter."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from tempfile import gettempdir
from typing import Any

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentResult,
    AgentRuntimeKind,
    AgentTask,
    AvailabilityReason,
    FilesystemAccess,
    PermissionMode,
    RuntimeAvailability,
    ToolCallAudit,
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
    tool_completed_event,
    tool_requested_event,
    vendor_turn_event,
)


class AntigravityAgentRuntime:
    """Run tasks through Google's ``google-antigravity`` SDK."""

    kind = AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK
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
        default_model: str = "gemini-3.5-flash",
        supported_models: tuple[str, ...] | None = None,
        api_key: str | None = None,
        agent_cls: Any | None = None,
        config_cls: Any | None = None,
        types_module: Any | None = None,
        policy_module: Any | None = None,
    ) -> None:
        self._default_model = default_model
        self._supported_models = supported_models
        self._api_key = api_key
        self._agent_cls = agent_cls
        self._config_cls = config_cls
        self._types = types_module
        self._policy = policy_module

    def availability(self) -> RuntimeAvailability:
        """Report Antigravity package and API-key availability."""

        if self._agent_cls is not None:
            return RuntimeAvailability.ok(self.kind, package="google-antigravity")
        package = package_availability(
            self.kind,
            module_name="google.antigravity",
            package_name="google-antigravity",
        )
        if not package.available:
            return package
        if self._api_key_value() is None:
            return RuntimeAvailability.unavailable(
                self.kind,
                reason=AvailabilityReason.MISSING_CREDENTIALS,
                message="Set GEMINI_API_KEY or GOOGLE_API_KEY to use Antigravity.",
                package="google-antigravity",
                metadata=package.metadata,
            )
        return package

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task with Antigravity."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            model = self._model(task)
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            sdk = self._load_sdk()
            api_key = self._api_key_value()
            if api_key is None:
                raise RuntimeError("Antigravity requires GEMINI_API_KEY or GOOGLE_API_KEY")
            config = self._build_config(task, model=model, api_key=api_key, sdk=sdk)
            result = await self._invoke(task, config=config, sdk=sdk, model=model)
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

        if result.error:
            await safe_emit(task, task_failed_event(task, self.kind, error=result.error))
        else:
            await safe_emit(task, task_completed_event(task, self.kind, result))
        return result

    async def cancel(self, task_id: str) -> None:
        """Antigravity cancellation is not exposed through this portable adapter yet."""

        del task_id

    def _load_sdk(self) -> _AntigravitySDK:
        if (
            self._agent_cls is not None
            and self._config_cls is not None
            and self._types is not None
            and self._policy is not None
        ):
            return _AntigravitySDK(self._agent_cls, self._config_cls, self._types, self._policy)
        try:
            from google.antigravity import types  # type: ignore[import-not-found]
            from google.antigravity.agent import Agent  # type: ignore[import-not-found]
            from google.antigravity.connections.local.local_connection_config import (  # type: ignore[import-not-found]
                LocalAgentConfig,
            )
            from google.antigravity.hooks import policy  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "google-antigravity is not installed. Install agent-runtime-kit[antigravity]."
            ) from exc
        return _AntigravitySDK(Agent, LocalAgentConfig, types, policy)

    def _build_config(
        self,
        task: AgentTask,
        *,
        model: str,
        api_key: str,
        sdk: _AntigravitySDK,
    ) -> Any:
        for server in task.mcp_servers:
            if server.env:
                raise UnsupportedTaskInputError(
                    self.kind,
                    "mcp_servers.env",
                    "Antigravity MCP stdio server config does not support env",
                )
        capabilities, policies = _capability_policy(task, sdk)
        schema = output_schema_from(task.output_schema, task.metadata)
        return sdk.config_cls(
            model=model,
            api_key=api_key,
            system_instructions=task.system,
            capabilities=capabilities,
            policies=policies,
            workspaces=_workspaces(task),
            conversation_id=_conversation_id(task),
            save_dir=str(_runtime_dir("antigravity-sessions")),
            app_data_dir=str(_runtime_dir("antigravity-app-data")),
            response_schema=dict(schema) if schema is not None else None,
            mcp_servers=[
                sdk.types.McpStdioServer(command=server.command, args=list(server.args))
                for server in task.mcp_servers
            ],
        )

    async def _invoke(
        self,
        task: AgentTask,
        *,
        config: Any,
        sdk: _AntigravitySDK,
        model: str,
    ) -> AgentResult:
        text_parts: list[str] = []
        tool_calls: list[ToolCallAudit] = []
        usage_metadata: Any | None = None
        structured_output: Any | None = None
        session_id: str | None = None

        async with sdk.agent_cls(config) as agent:
            response = await agent.chat(task.goal)
            async for chunk in response.chunks:
                await self._consume_chunk(
                    task,
                    chunk=chunk,
                    sdk=sdk,
                    text_parts=text_parts,
                    tool_calls=tool_calls,
                )
            structured_output = await _maybe_await(response.structured_output())
            usage_metadata = getattr(response, "usage_metadata", None)
            session_id = _optional_str(getattr(agent, "conversation_id", None))

        output = "".join(text_parts).strip()
        schema = output_schema_from(task.output_schema, task.metadata)
        if structured_output is None and schema is not None:
            structured_output = parse_json_output(output)
        if schema is not None and structured_output is None:
            return AgentResult(
                output=output,
                finish_reason="failed",
                error="Antigravity SDK returned no structured output for output_schema",
                usage=_usage_from(usage_metadata),
                tool_calls=tuple(tool_calls),
                session_id=session_id,
                metadata={"model": model, "sdk": "google_antigravity"},
            )
        return AgentResult(
            output=output,
            parsed_output=structured_output,
            usage=_usage_from(usage_metadata),
            tool_calls=tuple(tool_calls),
            session_id=session_id,
            rounds=1,
            metadata={"model": model, "sdk": "google_antigravity"},
        )

    async def _consume_chunk(
        self,
        task: AgentTask,
        *,
        chunk: Any,
        sdk: _AntigravitySDK,
        text_parts: list[str],
        tool_calls: list[ToolCallAudit],
    ) -> None:
        if isinstance(chunk, sdk.types.Text):
            text_parts.append(str(chunk.text))
            await safe_emit(task, output_delta_event(task, self.kind, text=str(chunk.text)))
            return
        if isinstance(chunk, sdk.types.Thought):
            await safe_emit(
                task,
                vendor_turn_event(
                    task,
                    self.kind,
                    payload={"chunk_type": "Thought", "delta_length": len(str(chunk.text))},
                ),
            )
            return
        if isinstance(chunk, sdk.types.ToolCall):
            await safe_emit(
                task,
                tool_requested_event(
                    task,
                    self.kind,
                    tool_name=_tool_name(getattr(chunk, "name", "tool")),
                    arguments=_tool_arguments(chunk),
                ),
            )
            return
        if isinstance(chunk, sdk.types.ToolResult):
            audit = ToolCallAudit(
                tool_name=_tool_name(getattr(chunk, "name", "tool")),
                arguments=_tool_arguments(chunk),
                result_preview=str(getattr(chunk, "result", ""))[:256],
                status="ok",
            )
            tool_calls.append(audit)
            await safe_emit(task, tool_completed_event(task, self.kind, audit))
            return
        await safe_emit(
            task,
            vendor_turn_event(task, self.kind, payload={"chunk_type": type(chunk).__name__}),
        )

    def _api_key_value(self) -> str | None:
        return self._api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def _model(self, task: AgentTask) -> str:
        return metadata_str(task.metadata, "model") or self._default_model


class _AntigravitySDK:
    def __init__(self, agent_cls: Any, config_cls: Any, types: Any, policy: Any) -> None:
        self.agent_cls = agent_cls
        self.config_cls = config_cls
        self.types = types
        self.policy = policy


def _capability_policy(task: AgentTask, sdk: _AntigravitySDK) -> tuple[Any, list[Any]]:
    builtin = sdk.types.BuiltinTools
    if task.permissions.allowed_tools == ():
        if task.permissions.filesystem is FilesystemAccess.READ_ONLY:
            tools = builtin.read_only()
        elif task.permissions.mode is PermissionMode.CAUTIOUS:
            tools = builtin.nondestructive()
        else:
            tools = builtin.all_tools()
    else:
        tools = list(task.permissions.allowed_tools)
    enable_subagents = (
        task.permissions.mode is PermissionMode.PERMISSIVE
        and getattr(builtin, "START_SUBAGENT", None) in tools
    )
    capabilities = sdk.types.CapabilitiesConfig(
        enabled_tools=tools,
        enable_subagents=enable_subagents,
    )
    policies = [] if task.permissions.mode is PermissionMode.STRICT else [sdk.policy.allow_all()]
    return capabilities, policies


def _workspaces(task: AgentTask) -> list[str]:
    if task.working_directory is None:
        return []
    return [str(task.working_directory)]


def _conversation_id(task: AgentTask) -> str | None:
    if task.resume_from is not None:
        return task.resume_from.session_id
    return task.session_id


def _runtime_dir(name: str) -> Path:
    path = Path(gettempdir()) / "agent-runtime-kit" / name
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _usage_from(value: Any) -> Usage:
    prompt_tokens = _optional_int(getattr(value, "prompt_token_count", None))
    output_tokens = _optional_int(getattr(value, "candidates_token_count", None))
    thoughts = _optional_int(getattr(value, "thoughts_token_count", None))
    cache_read = _optional_int(getattr(value, "cached_content_token_count", None))
    total = _optional_int(getattr(value, "total_token_count", None))
    return Usage(
        input_tokens=max(prompt_tokens - cache_read, 0),
        output_tokens=output_tokens + thoughts,
        cache_read_tokens=cache_read,
        total_tokens=total,
    )


def _tool_arguments(value: Any) -> Mapping[str, Any]:
    args = getattr(value, "args", {})
    return dict(args) if isinstance(args, Mapping) else {}


def _tool_name(value: Any) -> str:
    return str(getattr(value, "value", value) or "tool")


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


def _optional_str(value: Any) -> str | None:
    return str(value) if value else None


def _optional_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
