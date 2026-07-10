from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib

from agent_runtime_kit import (
    COMPATIBILITY_MANIFEST,
    AgentCapabilities,
    AgentKit,
    AgentResult,
    AgentRuntime,
    AgentRuntimeKind,
    AgentTask,
    McpServerConfig,
    PermissionProfile,
    RuntimeAvailability,
    SessionResumeState,
    TaskSupportIssue,
    TaskSupportReport,
    compatibility_for,
    validate_task,
)
from agent_runtime_kit.adapters import (
    AntigravityAgentRuntime,
    ClaudeAgentRuntime,
    CodexAgentRuntime,
)


class LegacyRuntime:
    """A protocol-complete third-party runtime predating task support reports."""

    kind = "x-legacy"
    capabilities = AgentCapabilities()

    def availability(self) -> RuntimeAvailability:
        return RuntimeAvailability.ok(self.kind)

    async def run(self, task: AgentTask) -> AgentResult:
        del task
        raise NotImplementedError

    async def cancel(self, task_id: str) -> None:
        del task_id

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> LegacyRuntime:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


class CustomSupportRuntime(LegacyRuntime):
    kind = "x-custom-support"
    capabilities = AgentCapabilities(working_directory=True)

    def validate_task(self, task: AgentTask) -> TaskSupportReport:
        del task
        return TaskSupportReport(
            self.kind,
            (TaskSupportIssue("model", "custom runtime rejected the selected model"),),
        )


def test_validate_task_reports_every_unsupported_field_without_raising(tmp_path: Path) -> None:
    task = AgentTask(
        goal="g",
        working_directory=tmp_path,
        mcp_servers=(McpServerConfig(name="repo", command="mcp", env={"TOKEN": "x"}),),
        permissions=PermissionProfile(allowed_tools=("Read",), network=False),
        session_id="session-1",
        output_schema={"type": "object"},
        budget_usd=1.0,
        reasoning_effort="high",
    )

    report = validate_task(LegacyRuntime(), task)

    assert report.supported is False
    assert [issue.field for issue in report.issues] == [
        "mcp_servers",
        "working_directory",
        "session_id",
        "output_schema",
        "budget_usd",
        "reasoning_effort",
        "permissions.network",
        "permissions.allowed_tools",
    ]


def test_validate_task_reports_resume_and_legacy_alias_source_fields() -> None:
    task = AgentTask(
        goal="g",
        resume_from=SessionResumeState("session-1"),
        metadata={
            "output_schema": {"type": "object"},
            "reasoning_effort": "high",
        },
    )

    report = validate_task(LegacyRuntime(), task)

    assert [issue.field for issue in report.issues] == [
        "resume_from",
        "metadata.output_schema",
        "metadata.reasoning_effort",
    ]


def test_validate_task_reports_mcp_env_separately() -> None:
    runtime = LegacyRuntime()
    runtime.capabilities = AgentCapabilities(mcp_support=True)
    task = AgentTask(
        goal="g",
        mcp_servers=(McpServerConfig(name="repo", command="mcp", env={"TOKEN": "x"}),),
    )

    report = validate_task(runtime, task)

    assert [issue.field for issue in report.issues] == ["mcp_servers.env"]


def test_builtin_runtime_reports_are_pure_and_provider_specific(tmp_path: Path) -> None:
    claude = ClaudeAgentRuntime()
    codex = CodexAgentRuntime()
    antigravity = AntigravityAgentRuntime()

    assert claude.validate_task(AgentTask(goal="g", budget_usd=1.0)).supported is True
    assert [
        issue.field
        for issue in claude.validate_task(
            AgentTask(goal="g", permissions=PermissionProfile(network=False))
        ).issues
    ] == ["permissions.network"]
    assert [
        issue.field
        for issue in codex.validate_task(
            AgentTask(
                goal="g",
                mcp_servers=(McpServerConfig(name="repo", command="mcp"),),
                budget_usd=1.0,
                reasoning_effort="high",
            )
        ).issues
    ] == ["mcp_servers", "budget_usd"]
    assert [
        issue.field
        for issue in antigravity.validate_task(
            AgentTask(
                goal="g",
                working_directory=tmp_path,
                mcp_servers=(McpServerConfig(name="repo", command="mcp", env={"X": "1"}),),
                reasoning_effort="high",
            )
        ).issues
    ] == ["mcp_servers.env", "reasoning_effort"]


def test_adapter_reports_configured_model_allowlist_at_source_field() -> None:
    runtimes = (
        ClaudeAgentRuntime(supported_models=("allowed",)),
        CodexAgentRuntime(supported_models=("allowed",)),
        AntigravityAgentRuntime(supported_models=("allowed",)),
    )

    for runtime in runtimes:
        first_class = runtime.validate_task(AgentTask(goal="g", model="blocked"))
        legacy = runtime.validate_task(AgentTask(goal="g", metadata={"model": "blocked"}))

        assert first_class.issues[-1].field == "model"
        assert legacy.issues[-1].field == "metadata.model"


def test_antigravity_reports_static_provider_constraints() -> None:
    runtime = AntigravityAgentRuntime()
    task = AgentTask(
        goal="g",
        mcp_servers=(McpServerConfig(name="not valid!", command="mcp"),),
        permissions=PermissionProfile(
            allowed_tools=("view_file",),
            disallowed_tools=("run_command",),
        ),
        metadata={"reasoning_effort": "high"},
    )

    report = runtime.validate_task(task)

    assert [issue.field for issue in report.issues] == [
        "metadata.reasoning_effort",
        "permissions.allowed_tools",
        "mcp_servers.name",
    ]


def test_agent_kit_and_registry_preserve_custom_runtime_validation() -> None:
    kit = AgentKit(include_fake=False, register_default_adapters=False)
    kit.registry.register(CustomSupportRuntime.kind, CustomSupportRuntime)
    task = AgentTask(goal="g")

    by_kind = kit.validate_task(CustomSupportRuntime.kind, task)
    by_instance = kit.validate_task(CustomSupportRuntime(), task)
    by_registry = kit.registry.validate_task_for(CustomSupportRuntime.kind, task)

    assert by_kind == by_instance == by_registry
    assert by_kind.issues[0].message.startswith("custom runtime")


def test_agent_runtime_protocol_remains_compatible_with_legacy_runtime() -> None:
    assert isinstance(LegacyRuntime(), AgentRuntime)


def test_task_support_report_rejects_non_issue_values() -> None:
    try:
        TaskSupportReport("x-bad", ("not-an-issue",))  # type: ignore[arg-type]
    except ValueError as exc:
        assert "TaskSupportIssue" in str(exc)
    else:  # pragma: no cover - assertion helper without pytest dependency
        raise AssertionError("invalid report value was accepted")


def test_compatibility_manifest_matches_project_and_lockfile() -> None:
    root = Path(__file__).parents[1]
    with (root / "pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)
    with (root / "uv.lock").open("rb") as stream:
        lock = tomllib.load(stream)

    optional = project["project"]["optional-dependencies"]
    locked_versions = {item["name"]: item["version"] for item in lock["package"]}
    assert len({entry.kind for entry in COMPATIBILITY_MANIFEST}) == len(
        COMPATIBILITY_MANIFEST
    )
    assert len({entry.package for entry in COMPATIBILITY_MANIFEST}) == len(
        COMPATIBILITY_MANIFEST
    )

    for entry in COMPATIBILITY_MANIFEST:
        assert optional[entry.extra] == [f"{entry.package}{entry.version_specifier}"]
        assert locked_versions[entry.package] == entry.tested_version
        assert compatibility_for(entry.kind) is entry
        for dependency in entry.tested_runtime_dependencies:
            assert locked_versions[dependency.package] == dependency.version

    codex = compatibility_for(AgentRuntimeKind.CODEX_AGENT_SDK)
    assert codex.tested_runtime_dependencies[0].package == "openai-codex-cli-bin"
