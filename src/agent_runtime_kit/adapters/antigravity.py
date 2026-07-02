"""Google Antigravity SDK runtime adapter."""

from __future__ import annotations

import asyncio
import enum
import importlib
import logging
import os
from collections.abc import Mapping
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
    close_vendor_resource,
    empty_completion_error,
    ensure_supported_model,
    filter_supported_kwargs,
    fingerprint_item,
    metadata_str,
    optional_int,
    optional_str,
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
    tool_completed_event,
    tool_requested_event,
    vendor_turn_event,
)

logger = logging.getLogger(__name__)


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
        """Report Antigravity package and credential availability.

        Synchronous by design (a setup diagnostic), but ADC discovery may read
        files or hit the GCE metadata server — from async code, call it via
        ``asyncio.to_thread`` like ``run()`` does internally.
        """

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
            if task.reasoning_effort:
                # LocalAgentConfig exposes no reasoning/thinking-effort control
                # (google-antigravity 0.1.x), so the first-class field must not
                # silently no-op. The legacy metadata alias stays ignored, as it
                # always has been for this adapter.
                raise UnsupportedTaskInputError(
                    self.kind,
                    "reasoning_effort",
                    "google-antigravity exposes no reasoning-effort control; "
                    "unset reasoning_effort for this runtime",
                )
            model = self._model(task)
            ensure_supported_model(
                kind=self.kind,
                model=model,
                supported_models=self._supported_models,
            )
            sdk = self._load_sdk()
            # Resolve auth off the event loop: ADC discovery can call
            # google.auth.default(), which reads files and may hit the GCE metadata
            # server synchronously — blocking the loop for other concurrent tasks.
            auth = await asyncio.to_thread(self._auth_config)
            if not auth.available:
                raise AgentRuntimeUnavailableError(
                    self.kind,
                    "Antigravity requires GEMINI_API_KEY, GOOGLE_API_KEY, or Google "
                    "Application Default Credentials with a Vertex AI project/location",
                )
            config, dropped = self._build_config(task, model=model, auth=auth, sdk=sdk)
            result = await self._invoke(
                task, config=config, sdk=sdk, model=model, dropped_options=dropped
            )
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
        """Close any reusable Antigravity agent process owned by this runtime.

        Acquires the run lock first so an external ``aclose()`` waits for an
        in-flight ``run()`` instead of closing the agent mid-turn.
        """

        async with self._agent_run_lock:
            await self._close_agent()

    async def _close_agent(self) -> None:
        """Close the agent holding only the agent lock (run lock assumed free)."""

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
        try:  # pragma: no cover - real SDK import, exercised via injected fakes in tests
            from google.antigravity import types
            from google.antigravity.agent import Agent
            from google.antigravity.connections.local.local_connection_config import (
                LocalAgentConfig,
            )
            from google.antigravity.hooks import policy
        except ImportError as exc:  # pragma: no cover
            raise AgentRuntimeUnavailableError(
                self.kind,
                "google-antigravity is not installed. Install agent-runtime-kit[antigravity].",
            ) from exc
        return _AntigravitySDK(Agent, LocalAgentConfig, types, policy)  # pragma: no cover

    def _build_config(
        self,
        task: AgentTask,
        *,
        model: str,
        auth: _AntigravityAuthConfig,
        sdk: _AntigravitySDK,
    ) -> tuple[Any, list[str]]:
        for server in task.mcp_servers:
            if server.env:
                raise UnsupportedTaskInputError(
                    self.kind,
                    "mcp_servers.env",
                    "Antigravity MCP stdio server config does not support env",
                )
        capabilities, policies = _capability_policy(self.kind, task, sdk)
        schema = output_schema_from(task.output_schema, task.metadata)
        config_kwargs: dict[str, Any] = {
            "model": model,
            "api_key": auth.api_key,
            "vertex": auth.vertex,
            "project": auth.project,
            "location": auth.location,
            "system_instructions": task.system,
            "capabilities": capabilities,
            "policies": policies,
            "workspaces": _workspaces(task),
            "conversation_id": _conversation_id(task),
            "save_dir": str(self._runtime_dir("antigravity-sessions")),
            "app_data_dir": str(self._runtime_dir("antigravity-app-data")),
            "response_schema": dict(schema) if schema is not None else None,
            "mcp_servers": [
                sdk.types.McpStdioServer(
                    name=server.name,
                    command=server.command,
                    args=list(server.args),
                )
                for server in task.mcp_servers
            ],
        }
        # Tolerate vendor option drift like Claude/Codex: drop kwargs the installed
        # LocalAgentConfig no longer accepts (instead of a TypeError) and record
        # them — except the tool posture (and workspace scoping when requested),
        # which must fail closed rather than run with the SDK's default access.
        required = ["capabilities", "policies"]
        if config_kwargs["workspaces"]:
            required.append("workspaces")
        supported, dropped = filter_supported_kwargs(
            sdk.config_cls, config_kwargs, required=required, kind=self.kind
        )
        return sdk.config_cls(**supported), dropped

    async def _invoke(
        self,
        task: AgentTask,
        *,
        config: Any,
        sdk: _AntigravitySDK,
        model: str,
        dropped_options: list[str] | None = None,
    ) -> AgentResult:
        text_parts: list[str] = []
        tool_calls: list[ToolCallAudit] = []
        usage_metadata: Any | None = None
        structured_output: Any | None = None
        session_id: str | None = None
        stop_reason: str | None = None
        process_reused = False
        schema = output_schema_from(task.output_schema, task.metadata)

        if self._reuse_process:
            async with self._agent_run_lock:
                try:
                    agent, process_reused = await self._persistent_agent(
                        task,
                        sdk=sdk,
                        config=config,
                    )
                    structured_output, usage_metadata, session_id, stop_reason = (
                        await self._chat_agent(
                            task,
                            agent=agent,
                            sdk=sdk,
                            text_parts=text_parts,
                            tool_calls=tool_calls,
                            wants_structured=schema is not None,
                        )
                    )
                except BaseException:
                    # Evict the reused agent on any non-normal exit — including
                    # cancellation (CancelledError is a BaseException) — so the
                    # next run() never reuses a poisoned process. The run lock is
                    # already held, so close under the agent lock only and never
                    # let cleanup mask the original error.
                    try:
                        await self._close_agent()
                    except Exception as close_exc:
                        logger.warning(
                            "failed to close Antigravity agent after run failure: %s",
                            close_exc,
                        )
                    raise
        else:
            async with sdk.agent_cls(config) as agent:
                structured_output, usage_metadata, session_id, stop_reason = (
                    await self._chat_agent(
                        task,
                        agent=agent,
                        sdk=sdk,
                        text_parts=text_parts,
                        tool_calls=tool_calls,
                        wants_structured=schema is not None,
                    )
                )

        # Fall back to the caller's conversation id when the SDK does not echo one,
        # so a resumed task always returns a usable session_id (matches Claude).
        session_id = session_id or _conversation_id(task)

        process_metadata = (
            self._process_reuse_metadata(process_reused) if self._reuse_process else None
        )
        metadata: dict[str, Any] = {
            "model": model,
            "sdk": "google_antigravity",
            **dict(process_metadata or {}),
        }
        if dropped_options:
            metadata["dropped_options"] = list(dropped_options)

        output = "".join(text_parts).strip()
        if structured_output is None and schema is not None:
            structured_output = parse_json_output(output)

        # A non-natural stop (token limit, safety block) is a failure, not a
        # successful completion of whatever partial text arrived first.
        terminal_reason, terminal_error = _map_stop_reason(stop_reason)
        if terminal_reason is not None:
            return AgentResult(
                output=output,
                finish_reason=terminal_reason,
                error=terminal_error,
                parsed_output=structured_output,
                usage=_usage_from(usage_metadata),
                tool_calls=tuple(tool_calls),
                session_id=session_id,
                rounds=1,
                metadata=metadata,
            )
        if schema is not None and structured_output is None:
            return AgentResult(
                output=output,
                finish_reason="failed",
                error=structured_output_unsatisfied_error("Antigravity SDK"),
                usage=_usage_from(usage_metadata),
                tool_calls=tuple(tool_calls),
                session_id=session_id,
                rounds=1,
                metadata=metadata,
            )
        if not output and not tool_calls and structured_output is None:
            return AgentResult(
                output="",
                finish_reason="failed",
                error=empty_completion_error("Antigravity SDK"),
                usage=_usage_from(usage_metadata),
                tool_calls=tuple(tool_calls),
                session_id=session_id,
                rounds=1,
                metadata=metadata,
            )
        return AgentResult(
            output=output,
            parsed_output=structured_output,
            usage=_usage_from(usage_metadata),
            tool_calls=tuple(tool_calls),
            session_id=session_id,
            rounds=1,
            metadata=metadata,
        )

    async def _chat_agent(
        self,
        task: AgentTask,
        *,
        agent: Any,
        sdk: _AntigravitySDK,
        text_parts: list[str],
        tool_calls: list[ToolCallAudit],
        wants_structured: bool,
    ) -> tuple[Any | None, Any | None, str | None, str | None]:
        response = await agent.chat(task.goal)
        async for chunk in response.chunks:
            await self._consume_chunk(
                task,
                chunk=chunk,
                sdk=sdk,
                text_parts=text_parts,
                tool_calls=tool_calls,
            )
        # Only pull native structured output when the caller asked for it; calling
        # structured_output() unconditionally either fabricates parsed_output or,
        # if the SDK errors when unconfigured, breaks every plain-text task.
        structured_output = (
            await _maybe_await(response.structured_output()) if wants_structured else None
        )
        usage_metadata = getattr(response, "usage_metadata", None)
        session_id = optional_str(getattr(agent, "conversation_id", None))
        return structured_output, usage_metadata, session_id, _response_stop_reason(response)

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
            try:
                agent = await enter() if callable(enter) else context
            except BaseException:
                try:
                    await close_vendor_resource(context)
                except Exception as close_exc:
                    logger.warning(
                        "failed to close Antigravity agent after startup failure: %s", close_exc
                    )
                raise
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
        await close_vendor_resource(context, fallback=agent)

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
            name = _tool_name(getattr(chunk, "name", "tool"))
            arguments = _tool_arguments(chunk)
            # Record the requested call now so result.tool_calls counts every
            # invocation (matching Claude/Codex), including ones that never emit a
            # ToolResult chunk. The matching result fills it in below.
            tool_calls.append(
                ToolCallAudit(tool_name=name, arguments=arguments, status="requested")
            )
            await safe_emit(
                task,
                tool_requested_event(task, self.kind, tool_name=name, arguments=arguments),
            )
            return
        if isinstance(chunk, sdk.types.ToolResult):
            audit = ToolCallAudit(
                tool_name=_tool_name(getattr(chunk, "name", "tool")),
                arguments=_tool_arguments(chunk),
                result_preview=str(getattr(chunk, "result", ""))[:256],
                status=_tool_result_status(chunk),
            )
            _attach_tool_result(tool_calls, audit)
            await safe_emit(task, tool_completed_event(task, self.kind, audit))
            return
        await safe_emit(
            task,
            vendor_turn_event(task, self.kind, payload={"chunk_type": type(chunk).__name__}),
        )

    def _vertex_auth_config(self) -> _AntigravityAuthConfig:
        project = (
            self._project
            or _env_first("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT")
            or _google_adc_project()
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

    def _auth_config(self) -> _AntigravityAuthConfig:
        # An explicit constructor api_key is the most specific request and wins.
        if self._api_key:
            return _AntigravityAuthConfig(api_key=self._api_key, source="api-key")
        # Explicit vertex=True takes precedence over an ambient env API key, so
        # AntigravityAgentRuntime(vertex=True, project=...) is not silently
        # redirected to the Gemini API just because GEMINI_API_KEY is exported.
        if self._vertex is True:
            return self._vertex_auth_config()
        ambient_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if ambient_key:
            return _AntigravityAuthConfig(api_key=ambient_key, source="api-key")
        if self._vertex is False:
            return _AntigravityAuthConfig(source="none")
        return self._vertex_auth_config()

    def _model(self, task: AgentTask) -> str:
        return task.model or metadata_str(task.metadata, "model") or self._default_model

    def _runtime_dir(self, name: str) -> Path:
        base = self._data_dir or _default_data_dir()
        path = base / name
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir does not chmod a pre-existing directory, so enforce the mode on the
        # leaf so session transcripts are not left world-readable. Only chmod dirs we
        # own: a caller may point data_dir at a shared/pre-existing tree we cannot
        # chmod, and a failed run is worse than a dir we did not tighten.
        if hasattr(os, "getuid"):
            try:
                if path.stat().st_uid == os.getuid():
                    os.chmod(path, 0o700)
            except OSError as exc:
                logger.warning("could not enforce 0o700 on %s: %s", path, exc)
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
        # mutually exclusive, so a deny-list either takes the disabled_tools route
        # (PERMISSIVE, whose baseline is every tool) or is folded into an allow-list
        # of baseline-minus-denied. Combining it with an explicit allow-list is
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
        if (
            task.permissions.filesystem is FilesystemAccess.READ_ONLY
            or task.permissions.mode is PermissionMode.STRICT
        ):
            # disabled_tools means "enable everything else", which under a
            # READ_ONLY filesystem (or STRICT's read-only posture) would leave
            # every unnamed write tool enabled — the deny-list twin of the
            # allow-list backdoor rejected below. Both constraints are
            # simultaneously expressible instead: enable the read-only toolset
            # minus the denied tools.
            denied = {getattr(tool, "value", tool) for tool in disabled}
            tools = [
                tool
                for tool in _read_only_tools(kind, "permissions.disallowed_tools", builtin)
                if getattr(tool, "value", tool) not in denied
            ]
            capabilities = sdk.types.CapabilitiesConfig(
                enabled_tools=tools,
                enable_subagents=_contains_tool(tools, subagent),
            )
        elif is_permissive:
            # PERMISSIVE's baseline is every tool, so the SDK's disabled_tools route
            # ("enable everything else") expresses baseline-minus-denied exactly.
            capabilities = sdk.types.CapabilitiesConfig(
                disabled_tools=disabled,
                enable_subagents=not _contains_tool(disabled, subagent),
            )
        else:
            # DEFAULT/CAUTIOUS: "enable everything else" would widen access past the
            # nondestructive baseline — denying one unrelated tool would bring back
            # run_command and every other destructive tool. Keep the deny-list
            # subtractive here too: baseline minus denied.
            denied = {getattr(tool, "value", tool) for tool in disabled}
            tools = [
                tool
                for tool in _nondestructive_tools(kind, "permissions.disallowed_tools", builtin)
                if getattr(tool, "value", tool) not in denied
            ]
            capabilities = sdk.types.CapabilitiesConfig(
                enabled_tools=tools,
                enable_subagents=_contains_tool(tools, subagent),
            )
    else:
        if task.permissions.allowed_tools == ():
            tools = _default_tools(task, builtin)
        else:
            tools = _validate_tools(kind, "allowed_tools", task.permissions.allowed_tools, builtin)
            if (
                task.permissions.filesystem is FilesystemAccess.READ_ONLY
                or task.permissions.mode is PermissionMode.STRICT
            ):
                # READ_ONLY — and STRICT, whose posture is read-only everywhere else
                # in this adapter (default tools, deny-lists) — is a hard constraint;
                # an explicit allow-list must not be a backdoor to write tools
                # (previously STRICT honored any named write tool). Reject rather
                # than silently widen access.
                _reject_non_read_only_tools(kind, tools, builtin)
        # Honor an explicitly allow-listed subagent tool regardless of mode; gating
        # this on PERMISSIVE left an explicit start_subagent request silently inert.
        enable_subagents = _contains_tool(tools, subagent)
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
        # Return real enum members so validated allow/deny lists match the type the
        # default-tool paths hand CapabilitiesConfig (previously plain strings).
        resolved.append(builtin(value))
    return resolved


def _read_only_tools(kind: AgentRuntimeKind, field: str, builtin: Any) -> list[Any]:
    """The SDK's read-only toolset, or a typed refusal when it cannot be resolved.

    Both the allow-list guard and the deny-list conversion depend on this set to
    enforce a READ_ONLY filesystem; if the installed SDK no longer exposes it
    (vendor drift), refusing is the only option that cannot widen access.
    """

    read_only = getattr(builtin, "read_only", None)
    if not callable(read_only):
        raise UnsupportedTaskInputError(
            kind,
            field,
            "the installed SDK exposes no read-only toolset to enforce a "
            "READ_ONLY filesystem against; refusing rather than widening access",
        )
    return list(read_only())


def _nondestructive_tools(kind: AgentRuntimeKind, field: str, builtin: Any) -> list[Any]:
    """The SDK's non-destructive toolset, or a typed refusal when it is missing.

    The DEFAULT/CAUTIOUS deny-list conversion subtracts from this baseline; if the
    installed SDK no longer exposes it (vendor drift), refusing is the only option
    that cannot widen access.
    """

    nondestructive = getattr(builtin, "nondestructive", None)
    if not callable(nondestructive):
        raise UnsupportedTaskInputError(
            kind,
            field,
            "the installed SDK exposes no non-destructive toolset to bound a "
            "deny-list against; refusing rather than widening access",
        )
    return list(nondestructive())


def _reject_non_read_only_tools(kind: AgentRuntimeKind, tools: list[Any], builtin: Any) -> None:
    allowed = {
        getattr(tool, "value", tool)
        for tool in _read_only_tools(kind, "permissions.allowed_tools", builtin)
    }
    for tool in tools:
        value = getattr(tool, "value", tool)
        if value not in allowed:
            ordered = ", ".join(sorted(str(item) for item in allowed))
            raise UnsupportedTaskInputError(
                kind,
                "permissions.allowed_tools",
                f"{value!r} is not permitted under a read-only posture "
                f"(READ_ONLY filesystem or STRICT mode); read-only tools are: {ordered}",
            )


def _builtin_tool_values(builtin: Any) -> set[str] | None:
    if not isinstance(builtin, enum.EnumMeta):
        return None
    members: list[Any] = list(builtin)
    return {str(getattr(member, "value", member)) for member in members}


def _attach_tool_result(tool_calls: list[ToolCallAudit], result_audit: ToolCallAudit) -> None:
    """Fill the most recent matching requested call with its result, else append.

    Keeps result.tool_calls one-entry-per-invocation: a ToolCall recorded the
    pending audit; its ToolResult replaces it here rather than adding a duplicate.
    Vendor chunks carry no correlation id, so matching is by tool name against
    the most recent pending call — exact for the sequential call/result pattern
    the SDK emits, best-effort if same-name calls ever interleave.
    """

    for index in range(len(tool_calls) - 1, -1, -1):
        existing = tool_calls[index]
        if existing.status == "requested" and existing.tool_name == result_audit.tool_name:
            tool_calls[index] = ToolCallAudit(
                tool_name=result_audit.tool_name,
                # ToolResult chunks may omit args; keep the request-time arguments
                # rather than degrading the audit to an empty mapping.
                arguments=result_audit.arguments or existing.arguments,
                result_preview=result_audit.result_preview,
                status=result_audit.status,
                duration_ms=result_audit.duration_ms,
            )
            return
    tool_calls.append(result_audit)


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
        return ("explicit-conversation", conversation_id, fingerprint_item(config))
    return ("task-isolated", task.task_id, fingerprint_item(config))


def _has_explicit_conversation_id_value(key: tuple[Any, ...] | None) -> bool:
    return bool(key and key[0] == "explicit-conversation")


def _default_data_dir() -> Path:
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "agent-runtime-kit"


def _usage_from(value: Any) -> Usage:
    prompt_tokens = optional_int(getattr(value, "prompt_token_count", None))
    output_tokens = optional_int(getattr(value, "candidates_token_count", None))
    thoughts = optional_int(getattr(value, "thoughts_token_count", None))
    cache_read = optional_int(getattr(value, "cached_content_token_count", None))
    total = optional_int(getattr(value, "total_token_count", None))
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


# Stop/finish reasons the SDK may report that still mean "completed normally".
_ANTIGRAVITY_SUCCESS_STOPS = frozenset(
    {"", "STOP", "FINISH_REASON_UNSPECIFIED", "END_TURN", "MODEL_FINISH"}
)


def _response_stop_reason(response: Any) -> str | None:
    """Best-effort read of a Gemini-style finish reason from the response.

    ASSUMES VENDOR BEHAVIOR: the exact attribute name is not verifiable without
    the installed SDK, so this probes the common shapes (``finish_reason`` /
    ``stop_reason`` on the response, then on the first candidate) and normalizes
    enums (which expose ``.name``) to an upper-case string. Returns ``None`` when
    no reason is exposed, which is treated as a normal completion.
    """

    raw = getattr(response, "finish_reason", None) or getattr(response, "stop_reason", None)
    if raw is None:
        candidates = getattr(response, "candidates", None)
        if isinstance(candidates, list | tuple):
            for candidate in candidates:
                raw = getattr(candidate, "finish_reason", None)
                if raw is not None:
                    break
    if raw is None:
        return None
    return str(getattr(raw, "name", raw) or "").upper()


def _map_stop_reason(stop_reason: str | None) -> tuple[str | None, str | None]:
    """Map a normalized stop reason to (finish_reason, error), or (None, None).

    (None, None) means "no override" — let the normal success/schema/empty logic
    decide. A token-limit truncation or a safety/recitation block is a failure,
    not a successful completion of whatever partial text arrived first.
    """

    if not stop_reason or stop_reason in _ANTIGRAVITY_SUCCESS_STOPS:
        return None, None
    if stop_reason == "MAX_TOKENS":
        return "max_tokens", "Antigravity response truncated at the output token limit"
    return "failed", f"Antigravity stopped early: {stop_reason}"
