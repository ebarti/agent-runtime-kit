# agent-runtime-kit

`agent-runtime-kit` is a small Python runtime layer for agent SDKs. It gives
applications one typed async API for dispatching an agentic task through Claude
Agent SDK, OpenAI Codex SDK, or Google Antigravity SDK while keeping provider
capabilities visible.

The package is intentionally not a router, benchmark harness, queue, hosted
service, or full agent framework. It is the reusable layer underneath those
systems: task models, runtime capabilities, event sinks, availability
diagnostics, and adapters.

## Install

Core only:

```bash
pip install agent-runtime-kit
```

Provider extras:

```bash
pip install "agent-runtime-kit[claude]"
pip install "agent-runtime-kit[codex]"
pip install "agent-runtime-kit[antigravity]"
pip install "agent-runtime-kit[all]"
```

```python
import asyncio

from agent_runtime_kit import AgentTask, FakeAgentRuntime


async def main() -> None:
    runtime = FakeAgentRuntime(output="done")
    result = await runtime.run(AgentTask(goal="Summarize this repository"))
    print(result.output)


asyncio.run(main())
```

The core package has no Claude, Codex, or Antigravity dependency. Vendor SDKs
are added through optional extras.

## Real Providers

```python
import asyncio

from agent_runtime_kit import AgentTask
from agent_runtime_kit.adapters import ClaudeAgentRuntime


async def main() -> None:
    runtime = ClaudeAgentRuntime(default_model="claude-sonnet-4-6")
    diagnostic = runtime.availability()
    if not diagnostic.available:
        raise RuntimeError(diagnostic.message)
    result = await runtime.run(AgentTask(goal="Summarize this repository"))
    print(result.output)


asyncio.run(main())
```

## Runtime Fields

`AgentTask` supports goal, system prompt, working directory, permission profile,
MCP stdio servers, session/resume handles, output schema, budget, metadata, and
an async event sink.

`AgentResult` returns output, finish reason, parsed structured output, usage,
cost, session id, artifacts, tool-call audits, and provider metadata.

## Docs

- [Quickstart](docs/quickstart.md)
- [Provider diagnostics](docs/providers.md)
- [Capability matrix](docs/capability-matrix.md)
- [Live smoke tests](docs/live-smoke.md)
- [Mestre migration notes](docs/mestre-migration.md)
- [Publish checklist](docs/publish-checklist.md)
