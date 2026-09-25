# Quickstart

`agent-runtime-kit` requires Python 3.10 or newer. Install the core package to
run the credential-free example:

```bash
python -m pip install agent-runtime-kit
```

If you cloned the repository, run examples from its root with
`python -m examples.basic_offline`. For a standalone script, save the following
as `offline.py` and run `python offline.py`:

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

Expected output: `offline example complete`. `FakeAgentRuntime` is a
deterministic test runtime. Its goal is carried through the API, but it does not
read files or call a model.

## Choose a provider

Install exactly the extra you need, or use `agent-runtime-kit[all]`:

```bash
python -m pip install "agent-runtime-kit[claude]"
python -m pip install "agent-runtime-kit[codex]"
python -m pip install "agent-runtime-kit[antigravity]"
```

Set up authentication with the provider's supported mechanism: [Claude Agent
SDK](https://code.claude.com/docs/en/agent-sdk/overview), [Codex
authentication](https://learn.chatgpt.com/docs/auth), or [Antigravity SDK
setup](https://github.com/google-antigravity/antigravity-sdk-python). For
example, Codex supports `codex login` and `codex login status`; Antigravity's
API-key path uses `GEMINI_API_KEY`. See [provider notes](providers.md) for this
package's exact adapter mappings.

From a source checkout, run one chosen provider:

```bash
python -m examples.provider_task claude
```

The command takes `claude`, `codex`, or `antigravity`. It may make a paid model
call. It checks the installed package and bounded readiness probe, then asks
the provider to summarize the current directory with explicit read-only
filesystem access. A confirmed `NOT_READY` result stops the example;
`INDETERMINATE` also stops until you explicitly pass `--attempt-indeterminate`.
That flag is a caller policy choice, not proof of readiness.

## Use the API directly

```python
import asyncio
from pathlib import Path

from agent_runtime_kit import AgentKit, ReadinessStatus


async def main() -> None:
    async with AgentKit() as kit:
        provider = "claude"
        availability = kit.availability_for(provider)
        if not availability.available:
            raise RuntimeError(availability.message)

        readiness = await kit.readiness_for(provider)
        if readiness.status is not ReadinessStatus.READY_TO_ATTEMPT:
            raise RuntimeError(readiness.message)

        result = await kit.run(
            provider,
            goal="Summarize the purpose of this repository in one paragraph.",
            working_directory=Path.cwd(),
            permissions="strict",
            filesystem="read-only",
        )
        if not result.is_success:
            raise RuntimeError(result.error or result.finish_reason)
        print(result.output)


asyncio.run(main())
```

`AgentKit` lazily resolves and caches built-in runtimes; `async with` closes
those it created. A runtime instance passed directly to `run` remains the
caller's responsibility to close. `permissions="strict"` alone does not
request read-only filesystem access: the default filesystem posture is
`workspace-write`. Each provider maps permission requests through its own SDK,
so check the [capability matrix](capability-matrix.md) before relying on a
particular tool posture.

For typed output and event examples, see the [runnable example
index](../examples/README.md). The [recipes](recipes.md) explain result
semantics, compatibility preflight, sessions, and MCP.
