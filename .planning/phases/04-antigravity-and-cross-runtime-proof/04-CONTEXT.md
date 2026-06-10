# Phase 4: Antigravity and Cross-Runtime Proof - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Add the Google Antigravity adapter, same-task three-runtime example, and tests
proving the shared API can represent Mestre's vendor-lane task fields.

</domain>

<decisions>
## Implementation Decisions

### Antigravity Mapping
- Map API key, model, system instructions, capabilities, policies,
  workspaces, conversation id, response schema, and MCP stdio servers.
- Reject MCP server env values because the SDK config shape does not expose
  them.
- Translate text, thought, tool call, and tool result chunks into normalized
  output/tool/vendor events.

### Cross-Runtime Proof
- Provide an example that registers all adapters and runs the same task shape
  through each available runtime.
- Keep unavailable providers as diagnostics rather than hard failures.

### the agent's Discretion
Keep Mestre compatibility as field coverage tests, not a Mestre dependency.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 3 adapter helpers and diagnostics patterns are reused.

### Established Patterns
- Vendor adapters support injected SDK surfaces in tests.

### Integration Points
- Provider diagnostics now include Antigravity.

</code_context>

<specifics>
## Specific Ideas

Mestre compatibility should prove representation of current vendor-lane inputs,
not migrate Mestre in this package.

</specifics>

<deferred>
## Deferred Ideas

Live smoke tests and release checklist are deferred to Phase 5.

</deferred>
