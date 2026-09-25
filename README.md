# agent-runtime-kit

`agent-runtime-kit` gives Python 3.10+ applications one typed, async API for
running coding-agent tasks with Claude Agent SDK, OpenAI Codex SDK, or Google
Antigravity SDK. It exposes capability checks, events, diagnostics, and results
while keeping each provider's permissions and supported inputs explicit.

## Install and try it offline

The core install has no vendor SDK or credential requirement:

```bash
python -m pip install agent-runtime-kit
```

```python
import asyncio

from agent_runtime_kit import AgentKit, FakeAgentRuntime


async def main() -> None:
    async with AgentKit() as kit:
        result = await kit.run(
            FakeAgentRuntime(output="offline example complete"),
            goal="Demonstrate a task result",
        )
        if not result.is_success:
            raise RuntimeError(result.error or result.finish_reason)
        print(result.output)


asyncio.run(main())
```

This prints `offline example complete`. The fake runtime returns a deterministic
result; it does not inspect the repository or contact a provider. Run the file
version with `python -m examples.basic_offline` from a source checkout.

## Run a real provider

Install only the provider you use, then configure its supported authentication:

```bash
python -m pip install "agent-runtime-kit[claude]"      # Claude Agent SDK
python -m pip install "agent-runtime-kit[codex]"       # OpenAI Codex SDK
python -m pip install "agent-runtime-kit[antigravity]" # Google Antigravity SDK
```

`agent-runtime-kit[all]` installs all three. A real task may use a paid provider
and may invoke tools. After setting up the chosen provider, run one explicitly
from a source checkout:

```bash
python -m examples.provider_task claude
```

Choose `codex` or `antigravity` instead to use that runtime. The example checks
package availability, probes setup, requests read-only filesystem access, and
checks `result.is_success`. Readiness only indicates whether a task is worth
attempting; it cannot guarantee the provider will accept the request.

The adapters share `AgentTask` and `AgentResult`, but inputs are not silently
discarded. For example, Claude supports per-task MCP servers and a cost budget;
Codex does not expose either per task; Antigravity supports MCP servers without
per-server environment values. Unsupported inputs raise typed errors. The
provider's native model selection is used unless you set a model explicitly.

## Guides

- [Quickstart](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/quickstart.md): install, offline result, and first provider run
- [Examples](https://github.com/ebarti/agent-runtime-kit/blob/main/examples/README.md): runnable commands and expected results
- [Recipes](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/recipes.md): results, events, preflight, permissions, sessions, and MCP
- [Provider diagnostics](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/providers.md) and [capability matrix](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/capability-matrix.md)
- [Deadlines and cancellation](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/task-control.md) and [API stability](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/api-stability.md)
- [Live smoke tests](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/live-smoke.md) and [SDK evolution agent](https://github.com/ebarti/agent-runtime-kit/blob/main/docs/sdk-evolution-agent.md)

The package is a runtime layer, not a router, queue, hosted service, or full
agent framework. Applications choose when and where to run a task.
