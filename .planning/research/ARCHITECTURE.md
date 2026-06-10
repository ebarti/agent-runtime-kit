# Architecture Research

**Domain:** Python multi-vendor agent runtime SDK
**Researched:** 2026-06-10
**Confidence:** HIGH

## Standard Architecture

### System Overview

```text
Caller application
    |
    v
Public API
  - AgentTask
  - AgentResult
  - AgentRuntime
  - EventSink
  - Runtime registry
    |
    v
Capability and validation layer
  - runtime availability
  - supported feature checks
  - permission/cwd/session/schema validation
    |
    v
Vendor adapter layer
  - ClaudeAgentRuntime
  - CodexAgentRuntime
  - AntigravityAgentRuntime
    |
    v
Vendor SDKs
  - claude-agent-sdk
  - openai-codex
  - google-antigravity
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| Public models | Stable task/result/event/capability types. | Dataclasses or Pydantic models with minimal dependency footprint. |
| Runtime protocol | Common async `run()` and `cancel()` contract. | `typing.Protocol` plus concrete base helpers. |
| Registry | Resolve runtime kinds to factories and capabilities. | Small dict-of-factories pattern from Mestre. |
| Availability checks | Report missing SDKs, auth, binaries, or unsupported models. | Per-adapter `available()` and `diagnose()` helpers. |
| Event sink | Allow callers to observe task starts, tool calls, output deltas, vendor turns, failures, and completions. | Async callable protocol with no-op default. |
| Claude adapter | Translate task inputs into Claude Agent SDK options and messages. | Adapter module behind optional `claude` extra. |
| Codex adapter | Manage Codex app-server/thread lifecycle and translate turn results. | Adapter module behind optional `codex` extra. |
| Antigravity adapter | Build local Antigravity config, policies, tools, workspace, and response translation. | Adapter module behind optional `antigravity` extra. |
| Compatibility layer | Map Mestre's current types to public package types. | Separate module or downstream Mestre adapter, not core dependency. |

## Recommended Project Structure

```text
src/agent_runtime_kit/
  __init__.py
  types.py                 # AgentTask, AgentResult, capabilities, sessions, audits
  runtime.py               # AgentRuntime protocol and base helpers
  events.py                # canonical event builders and event sink protocol
  registry.py              # runtime registration and resolution
  errors.py                # typed availability and runtime errors
  permissions.py           # neutral permission/capability model
  structured.py            # schema helpers and parsed-output validation
  adapters/
    __init__.py
    claude.py              # Claude Agent SDK adapter
    codex.py               # Codex SDK adapter
    antigravity.py         # Antigravity SDK adapter
  integrations/
    __init__.py
    opentelemetry.py       # optional tracing helpers
  compat/
    __init__.py
    mestre.py              # optional/initial migration bridge
tests/
  adapters/
  fixtures/
  test_registry.py
  test_types.py
docs/
  quickstart.md
  capabilities.md
  auth.md
  provider-notes.md
examples/
  run_same_task.py
```

### Structure Rationale

- **Core package stays small:** Public contract, registry, errors, permissions,
  structured output, and events are independent of vendor SDK imports.
- **Adapters are optional:** Each adapter module can fail gracefully when its
  extra is not installed.
- **Compatibility is separate:** Mestre migration helpers should not make the
  community package feel Mestre-specific.
- **Docs mirror the user journey:** Install, choose runtime, run task, inspect
  result, understand provider differences.

## Architectural Patterns

### Pattern 1: Capability-Gated Runtime Contract

**What:** Every runtime advertises static capabilities and validates task inputs
before invoking a vendor SDK.

**When to use:** Always. This is the main defense against hidden provider
differences.

**Trade-offs:** More explicit than a magical universal API, but much safer and
clearer.

**Example:**

```python
runtime = registry.resolve("codex")
if task.mcp_servers and not runtime.capabilities.mcp_support:
    raise UnsupportedFeature("codex runtime does not support per-task MCP config")
result = await runtime.run(task)
```

### Pattern 2: Optional Adapter Imports

**What:** Core imports never import vendor SDKs. Adapter modules import lazily
inside availability checks or invocation paths.

**When to use:** Required for optional extras and readable missing-dependency
errors.

**Trade-offs:** Slightly more boilerplate, but avoids broken imports for users
who only install one provider extra.

### Pattern 3: Event Sink Instead of Framework Lock-In

**What:** Runtime adapters emit generic async event dictionaries, with optional
helpers to translate to OpenTelemetry.

**When to use:** Users need observability but should not be forced into a
specific tracing backend.

**Trade-offs:** Event schemas need discipline and tests.

### Pattern 4: Compatibility Adapter, Not Public API Freeze

**What:** Keep the public API clean, then provide mapping helpers for Mestre's
existing `AgentTask`/`AgentResult` shape.

**When to use:** The source codebase is real and valuable, but the new package
should be community-first.

**Trade-offs:** One extra migration layer, but prevents public API from
inheriting too much product-specific vocabulary.

## Data Flow

### Runtime Invocation Flow

```text
User code
  -> create AgentTask
  -> resolve runtime
  -> validate task against capabilities
  -> emit agent.task.started
  -> invoke vendor SDK
  -> translate vendor messages, chunks, usage, sessions, and tools
  -> emit output/tool/vendor/completed events
  -> return AgentResult
```

### Adapter Availability Flow

```text
runtime.available()
  -> check optional dependency import
  -> check required local binary/app-server/auth where applicable
  -> return RuntimeAvailability(status, reason, remediation)
```

### Structured Output Flow

```text
AgentTask.output_schema
  -> adapter-specific schema conversion
  -> vendor SDK invocation
  -> parse/validate returned payload
  -> AgentResult.parsed_output or structured error
```

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| Single developer script | Use direct runtime resolution and no-op event sink. |
| Internal tool | Add event sink, capability checks, and explicit auth docs. |
| CI/automation | Use deterministic permissions, timeouts, structured output, and skipped live tests without credentials. |
| Larger platform | Add caller-owned routing, retries, queues, tracing, and persistence outside package core. |

### Scaling Priorities

1. **First bottleneck:** SDK surface drift. Fix with adapter boundary tests and version notes.
2. **Second bottleneck:** Observability payload growth. Fix with redaction and event truncation defaults.
3. **Third bottleneck:** Optional dependency conflicts. Fix with extras, loose compatible ranges, and isolated adapter imports.

## Anti-Patterns

### Anti-Pattern 1: Fake Uniformity

**What people do:** Make every provider accept the same inputs and silently drop
unsupported fields.

**Why it's wrong:** It hides safety and behavior differences.

**Do this instead:** Declare capabilities and fail clearly when an input cannot
be honored.

### Anti-Pattern 2: Core Imports Every Vendor SDK

**What people do:** Import all SDKs from top-level package modules.

**Why it's wrong:** Users who install one adapter get import failures for the
others.

**Do this instead:** Lazy imports and optional extras.

### Anti-Pattern 3: Extracting Mestre Wholesale

**What people do:** Move routing, fallback, benchmarks, optimization, and
runtime adapters into one package.

**Why it's wrong:** The result is not a focused community SDK.

**Do this instead:** Extract the runtime layer first and keep product policy in
Mestre.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Claude Agent SDK | Python package optional extra | Supports built-in tools, MCP, permissions, sessions, hooks, and structured output surfaces. |
| Codex SDK | Python package optional extra | Controls local Codex app-server; app-server model availability and SDK API drift are important. |
| Google Antigravity SDK | Python package optional extra | Uses local agent harness, API key auth, policies/capabilities, MCP, and compiled wheel runtime. |
| PyPI | Package publishing | `agent-runtime-kit` was available on 2026-06-10; recheck before publishing. |
| OpenTelemetry | Optional integration | Keep event sink generic; provide helper package/module if useful. |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Core models <-> adapters | Typed task/result/event objects | Core must not import vendor SDKs. |
| Registry <-> adapters | Runtime factory and capabilities | Allows custom/fake runtimes in tests. |
| Adapters <-> vendor SDKs | Lazy imports and vendor-specific translation | Most churn should stay inside adapter modules. |
| AgentSDK <-> Mestre | Compatibility adapter | Mestre should remain a consumer, not a dependency. |

## Sources

- `~/Github/mestre/mestre/vendor_lane/agent_protocol.py`
- `~/Github/mestre/mestre/vendor_lane/events.py`
- `~/Github/mestre/mestre/execution/agent/registry.py`
- `~/Github/mestre/mestre/vendor_lane/backends/claude_sdk.py`
- `~/Github/mestre/mestre/vendor_lane/backends/codex_sdk.py`
- `~/Github/mestre/mestre/vendor_lane/backends/antigravity_sdk.py`
- https://docs.anthropic.com/en/docs/claude-code/sdk
- https://developers.openai.com/codex/sdk
- https://github.com/google-antigravity/antigravity-sdk-python

---
*Architecture research for: Python multi-vendor agent runtime SDK*
*Researched: 2026-06-10*
