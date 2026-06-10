# agent-runtime-kit

`agent-runtime-kit` is a small Python runtime layer for agent SDKs. It gives
applications one typed async API for dispatching an agentic task while keeping
vendor-specific capabilities visible.

The package is intentionally not a router, benchmark harness, queue, hosted
service, or full agent framework. It is the reusable layer underneath those
systems: task models, runtime capabilities, event sinks, availability
diagnostics, and adapters.

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
are added through optional extras in later phases.
