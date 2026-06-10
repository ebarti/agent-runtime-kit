"""Vendor adapter modules."""

from agent_runtime_kit._types import AgentRuntimeKind
from agent_runtime_kit.adapters.claude import ClaudeAgentRuntime
from agent_runtime_kit.adapters.codex import CodexAgentRuntime
from agent_runtime_kit.registry import RuntimeRegistry

__all__ = [
    "ClaudeAgentRuntime",
    "CodexAgentRuntime",
    "register_adapters",
]


def register_adapters(registry: RuntimeRegistry, *, replace: bool = False) -> None:
    """Register the built-in vendor adapters in a runtime registry."""

    registry.register(AgentRuntimeKind.CLAUDE_AGENT_SDK, ClaudeAgentRuntime, replace=replace)
    registry.register(AgentRuntimeKind.CODEX_AGENT_SDK, CodexAgentRuntime, replace=replace)
