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

## Run a task

`AgentKit` is the hub: it registers the built-in runtimes, resolves them
lazily, and gives `run()` a keyword-native surface.

```python
import asyncio

from agent_runtime_kit import AgentKit


async def main() -> None:
    async with AgentKit() as kit:
        result = await kit.run(
            "claude",  # short alias; "claude-agent-sdk" works too
            goal="Summarize this repository",
            permissions="strict",
        )
        print(result.output)


asyncio.run(main())
```

## Typed structured output

Pass a type instead of hand-writing JSON schema. Dataclasses and `TypedDict`s
work out of the box (no dependency); Pydantic v2 models are used through their
own `model_json_schema`/`model_validate` when you have Pydantic installed.

```python
from dataclasses import dataclass

from agent_runtime_kit import AgentKit


@dataclass
class RepoSummary:
    name: str
    languages: list[str]
    risky_areas: list[str]


async def main() -> None:
    async with AgentKit() as kit:
        result = await kit.run(
            "claude",
            goal="Summarize this repository",
            output_type=RepoSummary,
        )
        if result.parsed is not None:
            print(result.parsed.languages)  # typed: list[str]
        else:
            print(result.error)
```

A payload that does not conform yields `finish_reason="failed"` with the
mismatch in `result.error` — the same convention the adapters use for
unsatisfied structured output. Unsupported annotations (sets, plain unions,
...) raise `OutputTypeError` at call time rather than sending a half-true
schema.

## Events and custom runtimes

```python
kit = AgentKit()


@kit.on("agent.tool.completed")
def log_tool(event) -> None:  # sync or async; exceptions never break a run
    print(event["summary"])


@kit.runtime("x-myorg-agent")
def my_runtime(**kwargs):  # must stay zero-arg constructible
    return MyRuntime(**kwargs)
```

## Lower-level use

The hub is sugar over the same objects you can use directly — construct an
adapter yourself when you need vendor-specific configuration, and pass either
the instance or a prebuilt `AgentTask` to `kit.run` (or call `runtime.run(task)`
without the hub at all):

```python
from agent_runtime_kit import AgentTask
from agent_runtime_kit.adapters import ClaudeAgentRuntime

runtime = ClaudeAgentRuntime(default_model="claude-sonnet-4-6", reuse_process=True)
result = await runtime.run(AgentTask(goal="Summarize this repository"))
```

Use `kit.availability()` (or `runtime.availability()`) before dispatching work
in applications that need clear setup diagnostics.
