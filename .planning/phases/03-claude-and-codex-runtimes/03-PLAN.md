# Phase 3: Claude and Codex Runtimes - Plan

## Plan 03-01: Claude Agent SDK adapter and tests

- Implement `ClaudeAgentRuntime`.
- Map task fields into `ClaudeAgentOptions`.
- Translate streamed SDK messages into `AgentResult`.
- Cover success, tools, sessions, diagnostics, and unsupported model handling.

## Plan 03-02: Codex SDK adapter and tests

- Implement `CodexAgentRuntime`.
- Map task fields into `AsyncCodex`, `CodexConfig`, thread start/resume, and
  thread run kwargs.
- Cover success, structured output, sessions, diagnostics, and unsupported MCP.

## Plan 03-03: One-runtime quickstart and provider diagnostics docs

- Add quickstart documentation using Claude.
- Add provider diagnostics documentation.
- Add a diagnostics helper for installed adapter availability.
