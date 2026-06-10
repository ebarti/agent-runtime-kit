from __future__ import annotations

from pathlib import Path

from agent_runtime_kit import (
    AgentRuntimeKind,
    AgentTask,
    FilesystemAccess,
    McpServerConfig,
    PermissionMode,
    PermissionProfile,
    SessionResumeState,
)
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)
from agent_runtime_kit.testing import RecordingEventSink


def test_public_task_can_represent_mestre_vendor_lane_fields(tmp_path: Path) -> None:
    sink = RecordingEventSink()
    task = AgentTask(
        task_id="mestre-task",
        goal="execute the vendor lane task",
        event_sink=sink,
        system="system",
        working_directory=tmp_path,
        mcp_servers=(McpServerConfig(name="repo", command="repo-mcp", args=("serve",)),),
        permissions=PermissionProfile(
            mode=PermissionMode.CAUTIOUS,
            filesystem=FilesystemAccess.WORKSPACE_WRITE,
            allowed_tools=("Read", "Edit"),
            disallowed_tools=("Bash",),
        ),
        sdk_executions=2,
        budget_usd=1.25,
        session_id="session-1",
        resume_from=SessionResumeState(session_id="session-1", transcript=({"x": 1},)),
        output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}},
        metadata={
            "model": "provider-model",
            "provider_model_id": "provider-model",
            "reasoning_effort": "high",
            "prompt_context_receipt": {"schema_version": 1},
        },
    )

    assert task.task_id == "mestre-task"
    assert task.event_sink is sink
    assert task.mcp_servers[0].name == "repo"
    assert task.resume_from is not None
    assert task.metadata["prompt_context_receipt"] == {"schema_version": 1}


def test_adapter_capabilities_cover_mestre_runtime_needs() -> None:
    capabilities = {
        AgentRuntimeKind.CLAUDE_AGENT_SDK: ClaudeAgentRuntime.capabilities,
        AgentRuntimeKind.CODEX_AGENT_SDK: CodexAgentRuntime.capabilities,
        AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK: AntigravityAgentRuntime.capabilities,
    }

    assert capabilities[AgentRuntimeKind.CLAUDE_AGENT_SDK].mcp_support is True
    assert capabilities[AgentRuntimeKind.CODEX_AGENT_SDK].session_resume is True
    assert capabilities[AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK].structured_output is True
    assert all(item.working_directory for item in capabilities.values())
