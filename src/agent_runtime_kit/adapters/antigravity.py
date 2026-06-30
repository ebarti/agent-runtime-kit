"""Google Antigravity SDK runtime adapter."""

from __future__ import annotations

import asyncio
import enum
import importlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runtime_kit._errors import AgentRuntimeUnavailableError, UnsupportedTaskInputError
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
        vertex: bool | None = None,
        project: str | None = None,
        location: str | None = None,
        data_dir: Path | None = None,
        agent_cls: Any | None = None,
        config_cls: Any | None = None,
        types_module: Any | None = None,
        policy_module: Any | None = None,
        reuse_process: bool = False,
    ) -> None:
        self._default_model = default_model
        self._supported_models = supported_models
        self._api_key = api_key
        self._vertex = vertex
        self._project = project
        self._location = location
        self._data_dir = data_dir
        self._agent_cls = agent_cls
        self._config_cls = config_cls
        self._types = types_module
        self._policy = policy_module
        self._reuse_process = reuse_process
        self._agent_context: Any | None = None
        self._agent: Any | None = None
        self._agent_key: tuple[Any, ...] | None = None
        self._sdk_process_start_count = 0
        self._sdk_process_reuse_count = 0
        self._agent_lock = asyncio.Lock()
        self._agent_run_lock = asyncio.Lock()

    async def __aenter__(self) -> AntigravityAgentRuntime:
        return self

    async def __aexit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        await self.aclose()

    def availability(self) -> RuntimeAvailability:
        """Report Antigravity package and credential availability."""

        if self._agent_cls is not None:
            return RuntimeAvailability.ok(self.kind, package="google-antigravity")
        package = package_availability(
            self.kind,
            module_name="google.antigravity",
            package_name="google-antigravity",
        )
        if not package.available:
            return package
        auth = self._auth_config()
        if not auth.available:
            return RuntimeAvailability.unavailable(
                self.kind,
                reason=AvailabilityReason.MISSING_CREDENTIALS,
                message=(
                    "Set GEMINI_API_KEY or GOOGLE_API_KEY, or configure Google "
                    "Application Default Credentials with a Vertex AI project/location."
                ),
                package="google-antigravity",
                metadata=package.metadata,
            )
        return RuntimeAvailability.ok(
            self.kind,
            package="google-antigravity",
            version=package.version,
            metadata={**dict(package.metadata), "auth_source": auth.source},
        )

    async def run(self, task: AgentTask) -> AgentResult:
        """Execute one task with Antigravity."""

        await safe_emit(task, task_started_event(task, self.kind))
        try:
            reject_unsupported_inputs(
                self.kind, task, budget=True, network=True, tool_filters=False
            )
            model = self._model(task)
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            sdk = self._load_sdk()
            auth = self._auth_config()
            if not auth.available:
                raise AgentRuntimeUnavailableError(
                    self.kind,
                    "Antigravity requires GEMINI_API_KEY, GOOGLE_API_KEY, or Google "
                    "Application Default Credentials with a Vertex AI project/location",
                )
            config = self._build_config(task, model=model, auth=auth, sdk=sdk)
            result = await self._invoke(task, config=config, sdk=sdk, model=model)
        except Exception as exc:
            await safe_emit(task, task_failed_event(task, self.kind, error=str(exc)))
            raise

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
        """Antigravity cancellation is not exposed through this portable adapter yet."""

        del task_id

    async def aclose(self) -> None:
        """Close any reusable Antigravity agent process owned by this runtime."""

        async with self._agent_lock:
            await self._close_agent_locked()

    def _load_sdk(self) -> _AntigravitySDK:
        if (
            self._agent_cls is not None
            and self._config_cls is not None
            and self._types is not None
            and self._policy is not None
        ):
            return _AntigravitySDK(self._agent_cls, self._config_cls, self._types, self._policy)
        try:
            from google.antigravity import types
            from google.antigravity.agent import Agent
            from google.antigravity.connections.local.local_connection_config import (
                LocalAgentConfig,
            )
            from google.antigravity.hooks import policy
        except ImportError as exc:
            raise AgentRuntimeUnavailableError(
                self.kind,
                "google-antigravity is not installed. Install agent-runtime-kit[antigravity].",
            ) from exc
        return _AntigravitySDK(Agent, LocalAgentConfig, types, policy)

    def _build_config(
        self,
        task: AgentTask,
        *,
        model: str,
        auth: _AntigravityAuthConfig,
        sdk: _AntigravitySDK,
    ) -> Any:
        for server in task.mcp_servers:
            if server.env:
                raise UnsupportedTaskInputError(
                    self.kind,
                    "mcp_servers.env",
                    "Antigravity MCP stdio server config does not support env",
                )
        capabilities, policies = _capability_policy(self.kind, task, sdk)
        schema = output_schema_from(task.output_schema, task.metadata)
        return sdk.config_cls(
            model=model,
            api_key=auth.api_key,
            vertex=auth.vertex,
            project=auth.project,
            location=auth.location,
            system_instructions=task.system,
            capabilities=capabilities,
            policies=policies,
            workspaces=_workspaces(task),
            conversation_id=_conversation_id(task),
            save_dir=str(self._runtime_dir("antigravity-sessions")),
            app_data_dir=str(self._runtime_dir("antigravity-app-data")),
            response_schema=dict(schema) if schema is not None else None,
            mcp_servers=[
                sdk.types.McpStdioServer(
                    name=server.name,
                    command=server.command,
                    args=list(server.args),
                )
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
        process_reused = False

        if self._reuse_process:
            async with self._agent_run_lock:
                try:
                    agent, process_reused = await self._persistent_agent(
                        task,
                        sdk=sdk,
                        config=config,
                    )
                    structured_output, usage_metadata, session_id = await self._chat_agent(
                        task,
                        agent=agent,
                        sdk=sdk,
                        text_parts=text_parts,
                        tool_calls=tool_calls,
                    )
                except Exception:
                    await self.aclose()
                    raise
        else:
            async with sdk.agent_cls(config) as agent:
                structured_output, usage_metadata, session_id = await self._chat_agent(
                    task,
                    agent=agent,
                    sdk=sdk,
                    text_parts=text_parts,
                    tool_calls=tool_calls,
                )

        process_metadata = (
            self._process_reuse_metadata(process_reused) if self._reuse_process else None
        )

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
                metadata={
                    "model": model,
                    "sdk": "google_antigravity",
                    **dict(process_metadata or {}),
                },
            )
        return AgentResult(
            output=output,
            parsed_output=structured_output,
            usage=_usage_from(usage_metadata),
            tool_calls=tuple(tool_calls),
            session_id=session_id,
            rounds=1,
            metadata={
                "model": model,
                "sdk": "google_antigravity",
                **dict(process_metadata or {}),
            },
        )

    async def _chat_agent(
        self,
        task: AgentTask,
        *,
        agent: Any,
        sdk: _AntigravitySDK,
        text_parts: list[str],
        tool_calls: list[ToolCallAudit],
    ) -> tuple[Any | None, Any | None, str | None]:
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
        return structured_output, usage_metadata, session_id

    async def _persistent_agent(
        self,
        task: AgentTask,
        *,
        sdk: _AntigravitySDK,
        config: Any,
    ) -> tuple[Any, bool]:
        key = _agent_key(task, config)
        async with self._agent_lock:
            if self._agent is not None and self._agent_key == key:
                self._sdk_process_reuse_count += 1
                return self._agent, True
            if self._agent is not None:
                await self._close_agent_locked()
            context = sdk.agent_cls(config)
            enter = getattr(context, "__aenter__", None)
            agent = await enter() if callable(enter) else context
            self._agent_context = context
            self._agent = agent
            self._agent_key = key
            self._sdk_process_start_count += 1
            return agent, False

    async def _close_agent_locked(self) -> None:
        context = self._agent_context
        agent = self._agent
        self._agent_context = None
        self._agent = None
        self._agent_key = None
        if context is not None:
            exit_method = getattr(context, "__aexit__", None)
            if callable(exit_method):
                await exit_method(None, None, None)
                return
        close = getattr(agent, "aclose", None) or getattr(agent, "close", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result

    def _process_reuse_metadata(self, reused: bool) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "sdk_process_reuse_enabled": self._reuse_process,
            "sdk_process_reused": reused,
            "sdk_process_start_count": self._sdk_process_start_count,
            "sdk_process_reuse_count": self._sdk_process_reuse_count,
        }
        if not _has_explicit_conversation_id_value(self._agent_key):
            metadata["sdk_process_reuse_scope"] = "task-isolated"
        return metadata

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
                status=_tool_result_status(chunk),
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

    def _auth_config(self) -> _AntigravityAuthConfig:
        api_key = self._api_key_value()
        if api_key:
            return _AntigravityAuthConfig(api_key=api_key, source="api-key")
        if self._vertex is False:
            return _AntigravityAuthConfig(source="none")

        adc_project = _google_adc_project()
        project = (
            self._project
            or _env_first("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")
            or adc_project
        )
        location = self._location or _env_first(
            "GOOGLE_CLOUD_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "CLOUD_ML_REGION",
        )
        if project:
            return _AntigravityAuthConfig(
                vertex=True,
                project=project,
                location=location or "global",
                source="application-default-credentials",
            )
        return _AntigravityAuthConfig(source="none")

    def _model(self, task: AgentTask) -> str:
        return metadata_str(task.metadata, "model") or self._default_model

    def _runtime_dir(self, name: str) -> Path:
        base = self._data_dir or _default_data_dir()
        path = base / name
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir does not chmod a pre-existing directory, so enforce the mode on the
        # leaf so session transcripts are not left world-readable.
        os.chmod(path, 0o700)
        return path


class _AntigravitySDK:
    def __init__(self, agent_cls: Any, config_cls: Any, types: Any, policy: Any) -> None:
        self.agent_cls = agent_cls
        self.config_cls = config_cls
        self.types = types
        self.policy = policy


@dataclass(frozen=True)
class _AntigravityAuthConfig:
    api_key: str | None = None
    vertex: bool | None = None
    project: str | None = None
    location: str | None = None
    source: str = "none"

    @property
    def available(self) -> bool:
        return self.api_key is not None or (
            self.vertex is True and self.project is not None and self.location is not None
        )


def _capability_policy(
    kind: AgentRuntimeKind, task: AgentTask, sdk: _AntigravitySDK
) -> tuple[Any, list[Any]]:
    builtin = sdk.types.BuiltinTools
    subagent = getattr(builtin, "START_SUBAGENT", None)
    is_permissive = task.permissions.mode is PermissionMode.PERMISSIVE
    if task.permissions.disallowed_tools:
        # The real CapabilitiesConfig requires enabled_tools and disabled_tools to be
        # mutually exclusive, so a deny-list takes the disabled_tools route and lets
        # the SDK enable everything else. Combining it with an explicit allow-list is
        # unrepresentable, so reject that rather than silently drop one.
        if task.permissions.allowed_tools:
            raise UnsupportedTaskInputError(
                kind,
                "permissions.allowed_tools",
                "Antigravity cannot combine an allow-list with a deny-list; "
                "set only one of allowed_tools or disallowed_tools",
            )
        disabled = _validate_tools(
            kind, "disallowed_tools", task.permissions.disallowed_tools, builtin
        )
        enable_subagents = is_permissive and not _contains_tool(disabled, subagent)
        capabilities = sdk.types.CapabilitiesConfig(
            disabled_tools=disabled,
            enable_subagents=enable_subagents,
        )
    else:
        if task.permissions.allowed_tools == ():
            tools = _default_tools(task, builtin)
        else:
            tools = _validate_tools(kind, "allowed_tools", task.permissions.allowed_tools, builtin)
        enable_subagents = is_permissive and _contains_tool(tools, subagent)
        capabilities = sdk.types.CapabilitiesConfig(
            enabled_tools=tools,
            enable_subagents=enable_subagents,
        )
    policies = [] if task.permissions.mode is PermissionMode.STRICT else [sdk.policy.allow_all()]
    return capabilities, policies


def _contains_tool(tools: list[Any], subagent: Any) -> bool:
    if subagent is None:
        return False
    target = getattr(subagent, "value", subagent)
    return any(getattr(tool, "value", tool) == target for tool in tools)


def _env_first(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _google_adc_project() -> str | None:
    try:
        google_auth = importlib.import_module("google.auth")
        _, project = google_auth.default()
    except Exception:
        return None
    return str(project) if project else None


def _default_tools(task: AgentTask, builtin: Any) -> list[Any]:
    mode = task.permissions.mode
    if task.permissions.filesystem is FilesystemAccess.READ_ONLY or mode is PermissionMode.STRICT:
        return list(builtin.read_only())
    if mode is PermissionMode.PERMISSIVE:
        return list(builtin.all_tools())
    # DEFAULT and CAUTIOUS get a non-destructive tool set (safety fix: DEFAULT no
    # longer grants run_command + all_tools).
    return list(builtin.nondestructive())


def _validate_tools(
    kind: AgentRuntimeKind, field: str, tools: tuple[str, ...], builtin: Any
) -> list[Any]:
    valid = _builtin_tool_values(builtin)
    if valid is None:
        # Injected fakes do not model an enum; pass values through untouched.
        return list(tools)
    resolved: list[Any] = []
    for tool in tools:
        value = getattr(tool, "value", tool)
        if value not in valid:
            ordered = ", ".join(sorted(valid))
            raise UnsupportedTaskInputError(
                kind,
                f"permissions.{field}",
                f"{value!r} is not an Antigravity built-in tool; valid values: {ordered}",
            )
        resolved.append(value)
    return resolved


def _builtin_tool_values(builtin: Any) -> set[str] | None:
    if not isinstance(builtin, enum.EnumMeta):
        return None
    members: list[Any] = list(builtin)
    return {str(getattr(member, "value", member)) for member in members}


def _tool_result_status(chunk: Any) -> str:
    if getattr(chunk, "error", None) is not None:
        return "error"
    if getattr(chunk, "exception", None) is not None:
        return "error"
    return "ok"


def _workspaces(task: AgentTask) -> list[str]:
    if task.working_directory is None:
        return []
    return [str(task.working_directory)]


def _conversation_id(task: AgentTask) -> str | None:
    if task.resume_from is not None:
        return task.resume_from.session_id
    return task.session_id


def _agent_key(task: AgentTask, config: Any) -> tuple[Any, ...]:
    conversation_id = _conversation_id(task)
    if conversation_id:
        return ("explicit-conversation", conversation_id, _fingerprint_item(config))
    return ("task-isolated", task.task_id, _fingerprint_item(config))


def _has_explicit_conversation_id_value(key: tuple[Any, ...] | None) -> bool:
    return bool(key and key[0] == "explicit-conversation")


def _fingerprint_item(value: Any) -> Any:
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, os.PathLike):
        return str(value)
    if isinstance(value, Mapping):
        return tuple(
            sorted((str(key), _fingerprint_item(item)) for key, item in value.items())
        )
    if isinstance(value, Iterable) and not isinstance(value, bytes | str | Mapping):
        return tuple(_fingerprint_item(item) for item in value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return (
            type(value).__module__,
            type(value).__qualname__,
            _fingerprint_item(model_dump(mode="python")),
        )
    if hasattr(value, "__dict__"):
        return (
            type(value).__module__,
            type(value).__qualname__,
            _fingerprint_item(vars(value)),
        )
    return (type(value).__module__, type(value).__qualname__, repr(value))


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agent-runtime-kit"


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
