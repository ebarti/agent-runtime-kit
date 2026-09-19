"""SDK surface contract tests.

These verify the real vendor SDK surfaces and configurations this package depends
on: no network, no credentials, no agent construction. Each SDK is gated
with ``importorskip`` so the suite still passes in the core-only lane.
"""

from __future__ import annotations

import dataclasses
import importlib.metadata
import inspect
from pathlib import Path

import pytest
from packaging.version import Version


def _fields(cls: object) -> set[str]:
    if dataclasses.is_dataclass(cls):
        return {f.name for f in dataclasses.fields(cls)}
    if hasattr(cls, "model_fields"):
        return set(cls.model_fields.keys())  # type: ignore[attr-defined]
    return set(inspect.signature(cls).parameters.keys())  # type: ignore[arg-type]


def test_claude_options_has_every_kwarg_adapter_builds() -> None:
    claude = pytest.importorskip("claude_agent_sdk")

    fields = _fields(claude.ClaudeAgentOptions)
    expected = {
        "model",
        "allowed_tools",
        "disallowed_tools",
        "permission_mode",
        "system_prompt",
        "cwd",
        "mcp_servers",
        "resume",
        "max_budget_usd",
        "output_format",
        "setting_sources",
    }
    missing = expected - fields
    assert not missing, f"ClaudeAgentOptions missing: {sorted(missing)}"


def test_claude_adapter_constructs_real_options(tmp_path: Path) -> None:
    claude = pytest.importorskip("claude_agent_sdk")
    from agent_runtime_kit import AgentTask, McpServerConfig, PermissionMode, PermissionProfile
    from agent_runtime_kit.adapters import ClaudeAgentRuntime

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    task = AgentTask(
        goal="Inspect configuration only",
        system="Return the requested structured result",
        working_directory=tmp_path,
        session_id="test-session",
        output_schema=schema,
        budget_usd=1.0,
        permissions=PermissionProfile(
            mode=PermissionMode.STRICT, allowed_tools=("Read",), disallowed_tools=("Bash",)
        ),
        mcp_servers=(McpServerConfig(name="files", command="test-mcp", args=("--read-only",)),),
    )
    options, dropped = ClaudeAgentRuntime()._build_options(task, None, claude.ClaudeAgentOptions)

    assert not dropped
    assert options.permission_mode == "plan"
    assert options.allowed_tools == ["Read"]
    assert options.disallowed_tools == ["Bash"]
    assert options.cwd == tmp_path
    assert options.resume == "test-session"
    assert options.max_budget_usd == 1.0
    assert options.output_format == {"type": "json_schema", "schema": schema}
    assert options.mcp_servers["files"]["args"] == ["--read-only"]


def test_claude_message_and_block_types_exist() -> None:
    claude = pytest.importorskip("claude_agent_sdk")

    assert {"content", "usage", "error", "session_id"} <= _fields(claude.AssistantMessage)
    assert "content" in _fields(claude.UserMessage)
    assert {"subtype", "is_error", "errors", "result", "structured_output"} <= _fields(
        claude.ResultMessage
    )
    assert "text" in _fields(claude.TextBlock)
    assert {"id", "name", "input"} <= _fields(claude.ToolUseBlock)
    assert {"tool_use_id", "is_error"} <= _fields(claude.ToolResultBlock)


def test_codex_exposes_expected_surface() -> None:
    codex = pytest.importorskip("openai_codex")

    assert hasattr(codex, "AsyncCodex")
    assert hasattr(codex, "CodexConfig")

    sandbox_values = {member.value for member in codex.Sandbox}
    assert {"read-only", "workspace-write", "full-access"} <= sandbox_values

    approval_names = {member.name for member in codex.ApprovalMode}
    assert {"auto_review", "deny_all"} <= approval_names

    run_params = set(inspect.signature(codex.AsyncThread.run).parameters.keys())
    assert {"approval_mode", "cwd", "effort", "model", "output_schema", "sandbox"} <= run_params

    start_params = set(inspect.signature(codex.AsyncCodex.thread_start).parameters.keys())
    assert {"developer_instructions", "model", "cwd", "approval_mode", "sandbox"} <= start_params


def test_codex_turn_result_status_and_error() -> None:
    codex = pytest.importorskip("openai_codex")

    fields = _fields(codex.TurnResult)
    assert {"status", "error", "final_response", "items", "usage"} <= fields


def test_codex_tool_call_item_types_carry_name_and_arguments() -> None:
    pytest.importorskip("openai_codex")
    from openai_codex.generated import v2_all as v2

    assert "command" in _fields(v2.CommandExecutionThreadItem)
    assert {"tool", "arguments"} <= _fields(v2.McpToolCallThreadItem)
    assert {"tool", "arguments"} <= _fields(v2.DynamicToolCallThreadItem)
    assert "query" in _fields(v2.WebSearchThreadItem)


def test_codex_thread_item_is_root_model_wrapper() -> None:
    pytest.importorskip("openai_codex")
    from openai_codex.generated import v2_all as v2
    from openai_codex.types import ThreadItem

    # TurnResult.items elements are RootModel wrappers; the adapter unwraps ``.root``
    # to reach the discriminated item. Guard that assumption.
    assert "root" in ThreadItem.model_fields
    command = v2.CommandExecutionThreadItem(
        id="c1",
        type="commandExecution",
        command="ls",
        status=v2.CommandExecutionStatus.completed,
        cwd="/tmp",
        command_actions=[],
    )
    wrapped = ThreadItem(root=command)
    assert wrapped.root.command == "ls"


def test_antigravity_types_surface() -> None:
    pytest.importorskip("google.antigravity")
    from google.antigravity import types

    for name in ("Text", "Thought", "ToolCall", "ToolResult", "CapabilitiesConfig"):
        assert hasattr(types, name), f"google.antigravity.types missing {name}"

    capabilities_fields = _fields(types.CapabilitiesConfig)
    assert {"enabled_tools", "disabled_tools", "enable_subagents"} <= capabilities_fields

    # ToolResult must expose an error indicator the adapter reads.
    assert {"error", "exception"} <= _fields(types.ToolResult)

    # BuiltinTools helper constructors the adapter relies on.
    assert types.BuiltinTools.read_only()
    assert types.BuiltinTools.nondestructive()
    assert types.BuiltinTools.all_tools()


def test_antigravity_mcp_stdio_server_requires_name() -> None:
    pytest.importorskip("google.antigravity")
    from google.antigravity import types
    from pydantic import ValidationError

    # Constructing WITHOUT name must raise (the adapter passes name=server.name).
    with pytest.raises(ValidationError):
        types.McpStdioServer(command="x", args=[])

    server = types.McpStdioServer(name="fs", command="x", args=[])
    assert server.name == "fs"


def test_antigravity_permissive_deny_list_preserves_other_tools() -> None:
    pytest.importorskip("google.antigravity")
    from google.antigravity import types
    from google.antigravity.connections import connection

    from agent_runtime_kit import AgentTask, PermissionMode, PermissionProfile
    from agent_runtime_kit.adapters.antigravity import AntigravityAgentRuntime, _capability_policy

    runtime = AntigravityAgentRuntime(api_key="test-no-network")
    task = AgentTask(
        goal="Inspect permissions only",
        permissions=PermissionProfile(
            mode=PermissionMode.PERMISSIVE, disallowed_tools=("run_command",)
        ),
    )
    capabilities, _ = _capability_policy(runtime.kind, task, runtime._load_sdk())
    expected = set(types.BuiltinTools.all_tools()) - {types.BuiltinTools.RUN_COMMAND}
    # 0.1.17 subtracts disabled_tools from default(), excluding ASK_QUESTION.
    # Earlier SDKs used all tools as that implicit baseline.
    resolver = getattr(connection, "resolve_active_tools", None)
    if resolver is not None:
        actual = resolver(capabilities)
    elif capabilities.enabled_tools is not None:
        actual = set(capabilities.enabled_tools)
    else:
        actual = set(types.BuiltinTools.all_tools()) - set(capabilities.disabled_tools or [])
    assert actual == expected


def test_antigravity_policy_and_config() -> None:
    pytest.importorskip("google.antigravity")
    from google.antigravity.connections.local.local_connection_config import LocalAgentConfig
    from google.antigravity.hooks import policy

    assert callable(policy.allow_all)

    fields = _fields(LocalAgentConfig)
    expected = {
        "model",
        "api_key",
        "system_instructions",
        "capabilities",
        "policies",
        "workspaces",
        "conversation_id",
        "save_dir",
        "app_data_dir",
        "response_schema",
        "mcp_servers",
    }
    missing = expected - fields
    assert not missing, f"LocalAgentConfig missing: {sorted(missing)}"


@pytest.mark.parametrize("vertex", [False, True])
def test_antigravity_adapter_constructs_explicit_api_key_endpoint(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, vertex: bool
) -> None:
    pytest.importorskip("google.antigravity")
    version = Version(importlib.metadata.version("google-antigravity"))
    if not vertex and version < Version("0.1.15"):
        pytest.skip("Endpoint model inspection uses Antigravity 0.1.15+")
    from google.antigravity import types

    from agent_runtime_kit import AgentTask
    from agent_runtime_kit._errors import UnsupportedTaskInputError
    from agent_runtime_kit.adapters import AntigravityAgentRuntime

    monkeypatch.setenv("GOOGLE_GENAI_USE_VERTEXAI", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "ambient-project")
    monkeypatch.setenv("GOOGLE_CLOUD_LOCATION", "ambient-location")
    monkeypatch.setenv("GEMINI_API_KEY", "ambient-key")
    runtime = AntigravityAgentRuntime(
        vertex=vertex, api_key="test-api-key-no-network", data_dir=tmp_path
    )
    def build_config():
        return runtime._build_config(
            AgentTask(goal="Inspect configuration only"),
            model=None,
            auth=runtime._auth_config(),
            sdk=runtime._load_sdk(),
        )

    if vertex and version < Version("0.1.16"):
        with pytest.raises(UnsupportedTaskInputError, match=r">=0\.1\.16"):
            build_config()
        return
    config, dropped = build_config()

    assert not dropped
    assert config.models
    for target in config.models:
        endpoint = target.endpoint
        expected_type = types.VertexEndpoint if vertex else types.GeminiAPIEndpoint
        assert isinstance(endpoint, expected_type)
        assert endpoint.api_key == "test-api-key-no-network"
        endpoint.validate_endpoint()
        if vertex:
            assert endpoint.project is None
            assert endpoint.location is None
