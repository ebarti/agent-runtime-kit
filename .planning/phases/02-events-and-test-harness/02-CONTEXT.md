# Phase 2: Events and Test Harness - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Add optional dependency extras, normalized runtime events, redaction/truncation
defaults, and credential-free fake SDK fixtures for adapter contract testing.

</domain>

<decisions>
## Implementation Decisions

### Events
- Use the canonical event names from Mestre's vendor lane.
- Emit dictionary events with `name`, `timestamp`, `summary`, and sanitized
  `attributes`.
- Redact common credential keys and truncate large string values by default.
- Treat event sinks as best-effort; sink failures must not abort runtimes.

### Test Harness
- Keep fake SDK fixtures under `agent_runtime_kit.testing`.
- Support scripted success, failure, missing dependency, unsupported input,
  timeout, session ids, structured output, and tool events.
- Use the fake SDK runtime as the shared contract surface for later adapters.

### the agent's Discretion
All implementation details are at the agent's discretion where they preserve
dependency isolation and later adapter testability.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 1 public models provide task/result/runtime primitives.

### Established Patterns
- Core package modules avoid vendor SDK imports.

### Integration Points
- `FakeAgentRuntime` now emits events through the same helpers real adapters
  will use.

</code_context>

<specifics>
## Specific Ideas

Default tests must continue to pass without Claude, Codex, or Antigravity SDKs
installed.

</specifics>

<deferred>
## Deferred Ideas

Real adapter implementations remain deferred to phases 3 and 4.

</deferred>
