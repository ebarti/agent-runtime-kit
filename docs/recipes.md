# Practical recipes

These recipes use the public API. Start with the [quickstart](quickstart.md)
and [runnable examples](../examples/README.md).

## Handle results and errors

`run()` can raise for invalid or unsupported inputs, missing runtimes, and
deadlines. It can also return an `AgentResult` whose `finish_reason` is not
`done` or whose `error` is set. Always check `is_success` before using output:

```python
result = await kit.run(
    "claude",
    goal="Summarize this repository",
    permissions="strict",
    filesystem="read-only",
)
if not result.is_success:
    raise RuntimeError(result.error or result.finish_reason)
print(result.output)
```

For `output_type=Summary`, `result.parsed` is a validated instance on a
successful non-null result. A mismatched payload becomes a failed result.
Use `result.parsed_output_available` to distinguish an absent structured
payload from valid JSON `null`; both have `parsed_output is None`. Usage and
cost fields are `None` when the provider does not report them, while reported
zero stays zero. Built-in runtimes currently leave `artifacts` empty.

## Separate preflight from setup

Use `kit.validate_task(provider, task)` to list statically detectable
unsupported fields without dispatching a task. Use
`kit.availability_for(provider)` for a synchronous, package-only check. Use
`await kit.readiness_for(provider)` for a bounded setup probe:

```python
from agent_runtime_kit import AgentTask, ReadinessStatus

task = AgentTask(goal="Summarize this repository")
report = kit.validate_task("codex", task)
if not report.supported:
    for issue in report.issues:
        print(issue.field, issue.message)

availability = kit.availability_for("codex")
if availability.available:
    readiness = await kit.readiness_for("codex")
    if readiness.status is ReadinessStatus.READY_TO_ATTEMPT:
        print("Setup detected; a task may be attempted")
```

These checks answer different questions. Static support does not prove that a
package is installed. Package availability does not prove credentials or
network access. `READY_TO_ATTEMPT` means setup was detected, not that a task
will succeed. `INDETERMINATE` requires an application policy decision; it is
not a positive readiness result. The [provider diagnostics](providers.md)
describe safe probe behavior.

## Request permissions explicitly

```python
result = await kit.run(
    "claude",
    goal="Summarize this repository",
    permissions="strict",
    filesystem="read-only",
)
```

The default filesystem request is `workspace-write`, even under `strict`.
Adapters map both settings to vendor controls; they are not a universal OS
sandbox guarantee. `permissions.network` has no supported per-task mapping in
the built-in adapters and is rejected. Tool allow/deny lists and approval
behavior differ by provider. Read the [capability matrix](capability-matrix.md)
before requesting a more permissive posture.

`sdk_executions` is an informational task/event field, not a portable turn or
spend cap. Only Claude maps `budget_usd`; Codex and Antigravity reject it.
Use [deadlines and cancellation](task-control.md) to bound a task's duration.

## Observe events

`AgentKit.on()` registers a sync or async handler for normalized event names.
Register before dispatch. Handler failures are swallowed so logging cannot
break a run; send events to your own durable sink if they must be retained.

```python
@kit.on("agent.tool.completed")
def log_tool(event):
    print(event["summary"])
```

See `python -m examples.events` for a complete offline run. Events may contain
redacted or truncated attributes; a tool audit is best-effort evidence of what
the vendor reported, not proof that no other side effect occurred.

## Resume a session when supported

Pass `session_id` or `resume_from` on a task, but not both. Save a returned
`result.session_id` if you need it later, and check session support for the
chosen runtime before reuse. An omitted handle starts a task-isolated
conversation; process reuse does not imply session reuse. Provider-specific
session behavior is documented in [provider notes](providers.md).

## Configure an MCP server where supported

```python
from agent_runtime_kit import AgentTask, McpServerConfig

task = AgentTask(
    goal="Use the local documentation server",
    mcp_servers=(McpServerConfig(name="docs", command="my-docs-mcp"),),
)
report = kit.validate_task("claude", task)
if not report.supported:
    raise ValueError(report.issues)
```

Claude and Antigravity accept per-task stdio MCP servers; Codex rejects
per-task MCP configuration. Antigravity does not accept per-server `env` values.
The command starts only when a real task is dispatched. Choose and trust the
server executable as you would any local process. See the [capability
matrix](capability-matrix.md) for other differences.
