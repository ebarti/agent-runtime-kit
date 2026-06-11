"""SDK surface contract tests.

These verify the real vendor SDK surfaces this package depends on. They are pure
introspection: no network, no credentials, no agent construction. Each SDK is gated
with ``importorskip`` so the suite still passes in the core-only lane.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest


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
