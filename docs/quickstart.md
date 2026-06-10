# Quickstart

Install the dependency-free core:

```bash
pip install agent-runtime-kit
```

Install one provider extra:

```bash
pip install "agent-runtime-kit[claude]"
```

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
