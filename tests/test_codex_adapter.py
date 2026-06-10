from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from agent_runtime_kit import (
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    McpServerConfig,
    PermissionProfile,
)
from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit.adapters import CodexAgentRuntime
from agent_runtime_kit.testing import RecordingEventSink


@dataclass
class FakeCodexConfig:
    cwd: str | None = None
    config_overrides: tuple[str, ...] = ()


class FakeSandbox:
    read_only = "read-only"
    workspace_write = "workspace-write"
    full_access = "full-access"


class FakeApprovalMode:
    auto_review = "auto-review"
    deny_all = "deny-all"


class FakeCodex:
    def __init__(self, config: FakeCodexConfig) -> None:
        self.config = config
        self.started_kwargs: dict[str, Any] | None = None

    async def __aenter__(self) -> FakeCodex:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def thread_start(self, **kwargs: Any) -> FakeThread:
        self.started_kwargs = kwargs
        return FakeThread("thread-new")

    async def thread_resume(self, thread_id: str, **kwargs: Any) -> FakeThread:
        self.started_kwargs = kwargs
        return FakeThread(thread_id)


class FakeThread:
    def __init__(self, thread_id: str) -> None:
        self.id = thread_id

    async def run(self, prompt: str, **kwargs: Any) -> Any:
        return {
            "final_response": '{"ok": true}' if kwargs.get("output_schema") else f"done: {prompt}",
            "usage": {
                "total": {
                    "input_tokens": 4,
                    "output_tokens": 6,
                    "cached_input_tokens": 1,
                    "total_tokens": 11,
                }
            },
        }


@pytest.mark.asyncio
async def test_codex_runtime_runs_with_injected_sdk() -> None:
    sink = RecordingEventSink()
    runtime = CodexAgentRuntime(
        codex_cls=FakeCodex,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    result = await runtime.run(
        AgentTask(
            goal="implement",
            event_sink=sink,
            output_schema={"type": "object"},
            permissions=PermissionProfile(filesystem=FilesystemAccess.READ_ONLY),
        )
    )

    assert result.output == '{"ok": true}'
    assert result.parsed_output == {"ok": True}
    assert result.usage.input_tokens == 4
    assert result.session_id == "thread-new"
    assert sink.events[-1]["name"] == "agent.task.completed"


@pytest.mark.asyncio
async def test_codex_runtime_rejects_mcp_servers() -> None:
    runtime = CodexAgentRuntime(
        codex_cls=FakeCodex,
        config_cls=FakeCodexConfig,
        sandbox_cls=FakeSandbox,
        approval_mode_cls=FakeApprovalMode,
    )

    with pytest.raises(UnsupportedTaskInputError):
        await runtime.run(
            AgentTask(
                goal="x",
                mcp_servers=(McpServerConfig(name="fs", command="mcp"),),
            )
        )


def test_codex_availability_uses_injected_sdk() -> None:
    runtime = CodexAgentRuntime(codex_cls=FakeCodex, config_cls=FakeCodexConfig)

    diagnostic = runtime.availability()

    assert diagnostic.kind is AgentRuntimeKind.CODEX_AGENT_SDK
    assert diagnostic.available is True
