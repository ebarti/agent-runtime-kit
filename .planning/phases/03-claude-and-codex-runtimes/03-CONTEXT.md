# Phase 3: Claude and Codex Runtimes - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Implement runnable Claude Agent SDK and OpenAI Codex SDK adapters through the
shared runtime API, with clear diagnostics and fake-injected tests.

</domain>

<decisions>
## Implementation Decisions

### Adapter Boundaries
- Keep imports lazy so `agent_runtime_kit` remains dependency-free.
- Expose real adapter classes under `agent_runtime_kit.adapters`.
- Use injected SDK surfaces in tests rather than live credentials.
- Reject unsupported provider inputs with typed errors.

### Provider Mapping
- Claude maps system prompt, cwd, permissions, MCP, sessions, structured
  output, allowed/disallowed tools, and budget where supported.
- Codex maps cwd, developer instructions, model, approval mode, sandbox,
  sessions, structured output, and reasoning effort.
- Codex rejects per-task MCP because the Python SDK does not expose that
  configuration path.

### the agent's Discretion
Use generic, community-facing names while preserving Mestre-compatible fields.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 2 events and fake SDK helpers provide adapter test surfaces.
- Mestre's Claude and Codex vendor-lane adapters informed mapping choices.

### Established Patterns
- Runtime adapters emit normalized events and return `AgentResult`.

### Integration Points
- `agent_runtime_kit.adapters.register_adapters()` registers provider runtimes
  into the core registry.

</code_context>

<specifics>
## Specific Ideas

The quickstart should demonstrate one provider end to end through the public
API.

</specifics>

<deferred>
## Deferred Ideas

Antigravity and cross-runtime proof are deferred to Phase 4.

</deferred>
