"""Pure task-to-runtime compatibility checks."""

from __future__ import annotations

from collections.abc import Mapping

from agent_runtime_kit._errors import UnsupportedTaskInputError
from agent_runtime_kit._types import (
    AgentCapabilities,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    TaskSupportIssue,
    TaskSupportReport,
)


def _validate_declared_task_support(
    kind: AgentRuntimeKind | str,
    capabilities: AgentCapabilities,
    task: AgentTask,
) -> TaskSupportReport:
    """Report every task field the declared runtime capabilities cannot honor."""

    issues: list[TaskSupportIssue] = []
    if task.mcp_servers and not capabilities.mcp_support:
        issues.append(
            TaskSupportIssue(
                "mcp_servers",
                "runtime does not support per-task MCP server configuration",
            )
        )
    elif not capabilities.mcp_server_env:
        if any(server.env for server in task.mcp_servers):
            issues.append(
                TaskSupportIssue(
                    "mcp_servers.env",
                    "runtime does not support environment values on MCP server configuration",
                )
            )
    if task.working_directory is not None and not capabilities.working_directory:
        issues.append(
            TaskSupportIssue(
                "working_directory",
                "runtime does not support per-task working directories",
            )
        )
    if task.session_id is not None and not capabilities.session_resume:
        issues.append(
            TaskSupportIssue("session_id", "runtime does not support session resume")
        )
    if task.resume_from is not None and not capabilities.session_resume:
        issues.append(
            TaskSupportIssue("resume_from", "runtime does not support session resume")
        )
    if task.output_schema is not None and not capabilities.structured_output:
        issues.append(
            TaskSupportIssue("output_schema", "runtime does not support structured output")
        )
    elif not capabilities.structured_output:
        for key in ("output_schema", "json_schema"):
            if isinstance(task.metadata.get(key), Mapping):
                issues.append(
                    TaskSupportIssue(
                        f"metadata.{key}",
                        "runtime does not support structured output",
                    )
                )
                break
    if task.budget_usd is not None and not capabilities.budget:
        issues.append(
            TaskSupportIssue("budget_usd", "runtime does not expose a cost budget")
        )
    if task.reasoning_effort is not None and not capabilities.reasoning_effort:
        issues.append(
            TaskSupportIssue(
                "reasoning_effort",
                "runtime does not expose reasoning-effort control",
            )
        )
    elif not capabilities.reasoning_effort:
        legacy_effort = task.metadata.get("reasoning_effort")
        if isinstance(legacy_effort, str) and legacy_effort.strip():
            issues.append(
                TaskSupportIssue(
                    "metadata.reasoning_effort",
                    "runtime does not expose reasoning-effort control",
                )
            )
    if task.permissions.network is not None and not capabilities.network_control:
        issues.append(
            TaskSupportIssue(
                "permissions.network",
                "runtime does not expose network access control",
            )
        )
    if not capabilities.tool_filters:
        if task.permissions.allowed_tools:
            issues.append(
                TaskSupportIssue(
                    "permissions.allowed_tools",
                    "runtime does not expose a tool allow-list",
                )
            )
        if task.permissions.disallowed_tools:
            issues.append(
                TaskSupportIssue(
                    "permissions.disallowed_tools",
                    "runtime does not expose a tool deny-list",
                )
            )
    return TaskSupportReport(kind=kind, issues=tuple(issues))


def validate_task(runtime: AgentRuntime, task: AgentTask) -> TaskSupportReport:
    """Report task incompatibilities without starting a run.

    Runtimes may implement the optional :class:`TaskSupportProvider` protocol
    for provider- or instance-specific rules. Older third-party runtimes remain
    supported and fall back to their declared capabilities.
    """

    validator = getattr(runtime, "validate_task", None)
    if callable(validator):
        report = validator(task)
        if not isinstance(report, TaskSupportReport):
            raise TypeError("runtime.validate_task() must return TaskSupportReport")
        return report
    return _validate_declared_task_support(runtime.kind, runtime.capabilities, task)


def require_task_support(report: TaskSupportReport) -> None:
    """Raise the established typed error for the report's first issue."""

    if report.supported:
        return
    issue = report.issues[0]
    raise UnsupportedTaskInputError(report.kind, issue.field, issue.message)
