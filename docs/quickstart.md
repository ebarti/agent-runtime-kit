# Quickstart

Install all first-party runtime adapters:

```bash
pip install "agent-runtime-kit[all]"
```

Or install only the dependency-free core:

```bash
pip install agent-runtime-kit
```

Install one provider extra when you know you only need that runtime:

```bash
pip install "agent-runtime-kit[claude]"
```

The extras split is intentional. `agent-runtime-kit` exposes one API either way,
but Claude, Codex, and Antigravity ship as separate, fast-moving SDKs with their
own transitive dependencies and runtime packages. Optional extras keep a
Claude-only application from installing Codex or Antigravity packages, keep the
core usable when one vendor package is unavailable on a platform, and make
missing-provider errors actionable.

Run a task:

```python
import asyncio

from agent_runtime_kit import AgentTask
from agent_runtime_kit.adapters import ClaudeAgentRuntime


async def main() -> None:
    runtime = ClaudeAgentRuntime(default_model="claude-sonnet-4-6")
    result = await runtime.run(AgentTask(goal="Summarize this repository"))
    print(result.output)


asyncio.run(main())
```

Use `runtime.availability()` before dispatching work in applications that need
clear setup diagnostics.
