# Phase 1: Core Runtime Skeleton - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Create the dependency-free package core for `agent-runtime-kit`: metadata,
public task/result/runtime/capability/error models, a fake runtime, registry,
and baseline verification commands.

</domain>

<decisions>
## Implementation Decisions

### Runtime API
- Keep the public API close to Mestre's vendor-lane contract while removing
  all Mestre-specific imports.
- Use Python dataclasses, enums, and protocols for the core to avoid mandatory
  runtime dependencies.
- Keep `finish_reason` as a string so vendors can surface native sentinel
  values without lossy mapping.
- Put vendor-specific fields in `metadata` while preserving first-class fields
  for sessions, MCP, permissions, structured output, usage, and tool audits.

### Package Shape
- Use `src/agent_runtime_kit` as the import package for the PyPI project
  `agent-runtime-kit`.
- Target Python 3.10+ and include `py.typed`.
- Keep the fake runtime dependency-free and deterministic.
- Configure ruff, mypy, and pytest from the start.

### the agent's Discretion
All implementation choices are at the agent's discretion where they do not
conflict with the roadmap or Mestre compatibility requirement.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Mestre's `mestre/vendor_lane/agent_protocol.py` provided the starting shape
  for task, result, capability, session, MCP, and tool audit models.

### Established Patterns
- Existing repo only contained a uv skeleton; no local package conventions had
  to be preserved.

### Integration Points
- Future phases will add vendor adapters under `agent_runtime_kit.adapters`.

</code_context>

<specifics>
## Specific Ideas

The core package must import with no Claude, Codex, or Antigravity SDKs
installed.

</specifics>

<deferred>
## Deferred Ideas

Vendor events, fake SDK contract helpers, and real SDK adapters are deferred to
later roadmap phases.

</deferred>
