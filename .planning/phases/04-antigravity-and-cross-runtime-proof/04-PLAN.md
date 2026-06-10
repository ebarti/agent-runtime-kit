# Phase 4: Antigravity and Cross-Runtime Proof - Plan

## Plan 04-01: Google Antigravity SDK adapter and tests

- Implement `AntigravityAgentRuntime`.
- Map task fields into `LocalAgentConfig`.
- Translate text, thought, tool call, and tool result chunks.
- Cover success, structured output, tool events, diagnostics, and unsupported
  MCP env values.

## Plan 04-02: Same-task three-runtime example

- Add an example that registers Claude, Codex, and Antigravity adapters.
- Run one `AgentTask` shape through every available provider.
- Print setup diagnostics for unavailable providers.

## Plan 04-03: Mestre compatibility field audit and tests

- Add tests proving `AgentTask` covers Mestre's current vendor-lane fields.
- Add adapter capability assertions for Mestre-relevant fields.
- Add migration notes documenting the intended adoption boundary.
