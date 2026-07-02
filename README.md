# agent-runtime-kit

`agent-runtime-kit` is a small Python runtime layer for agent SDKs. It gives
applications one typed async API for dispatching an agentic task through Claude
Agent SDK, OpenAI Codex SDK, or Google Antigravity SDK while keeping provider
capabilities visible.

**Tags:** `agent-sdk` `agent-runtime` `coding-agents` `claude-code`
`openai-codex` `google-antigravity` `mcp` `typed-python` `async-python`
`developer-tools`

## About

`agent-runtime-kit` is for Python developers who want to run coding-agent tasks
through vendor runtimes without rewriting their application around each SDK. It
normalizes the runtime boundary: task inputs, capability checks, event streams,
tool audits, availability diagnostics, and typed results.

The library keeps vendor differences visible. Claude, Codex, and Antigravity
still expose different capabilities, permission models, setup requirements, and
unsupported fields. `agent-runtime-kit` gives those differences a consistent
shape instead of hiding them behind a lowest-common-denominator wrapper.

The package is intentionally not a router, benchmark harness, queue, hosted
service, or full agent framework. It is the reusable layer underneath those
systems: task models, runtime capabilities, event sinks, availability
diagnostics, and adapters.

## Install

If you want all first-party runtimes available, install the `all` extra:

```bash
pip install "agent-runtime-kit[all]"
```

Install the dependency-free core when you only need the public models, fake
runtime, registry, diagnostics types, or you plan to add provider SDKs later:

```bash
pip install agent-runtime-kit
```

Install a single provider extra when your application only dispatches through
one vendor runtime:

```bash
pip install "agent-runtime-kit[claude]"
pip install "agent-runtime-kit[codex]"
pip install "agent-runtime-kit[antigravity]"
```

Provider extras are a packaging boundary, not a separate API. They keep the
core importable without vendor SDKs, avoid forcing every user to install every
CLI binary or compiled runtime wheel, and contain dependency drift when one
fast-moving vendor SDK changes independently of the others. Missing adapters
raise typed setup errors that point to the matching extra.

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

`AgentKit` is the keyword-native hub: it registers the built-in runtimes,
caches them per kind, and turns Python types into structured output.

```python
import asyncio
from dataclasses import dataclass

from agent_runtime_kit import AgentKit


@dataclass
class RepoSummary:
    name: str
    languages: list[str]


async def main() -> None:
    async with AgentKit() as kit:
        diagnostic = kit.availability_for("claude")
        if not diagnostic.available:
            raise RuntimeError(diagnostic.message)
        result = await kit.run(
            "claude",
            goal="Summarize this repository",
            permissions="strict",
            output_type=RepoSummary,
        )
        print(result.parsed.languages if result.parsed else result.error)


asyncio.run(main())
```

Adapters also work standalone when you need vendor-specific configuration:

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

`AgentTask` supports goal, system prompt, model, reasoning effort, working
directory, permission profile, MCP stdio servers, session/resume handles, output
schema, budget, metadata, and an async event sink. (`model` and
`reasoning_effort` are first-class fields; the legacy `metadata["model"]` /
`metadata["reasoning_effort"]` aliases still work.) Where a runtime cannot honor
a field (for example only Claude maps `budget_usd`; Codex and Antigravity reject
it with a typed `UnsupportedTaskInputError`) the adapter raises rather than
silently dropping it.

`AgentResult` returns output, finish reason (see `FinishReason`), parsed
structured output, usage, cost, session id, tool-call audits, and provider
metadata. `artifacts` is a reserved field: no built-in runtime populates it yet,
so it is always an empty tuple today.

## Docs

- [Quickstart](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/quickstart.md)
- [Provider diagnostics](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/providers.md)
- [Capability matrix](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/capability-matrix.md)
- [API stability](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/api-stability.md)
- [Live smoke tests](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/live-smoke.md)
- [Mestre migration notes](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/mestre-migration.md)
- [SDK evolution agent](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/sdk-evolution-agent.md)
