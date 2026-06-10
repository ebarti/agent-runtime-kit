# Phase 3: Claude and Codex Runtimes - Summary

Implemented the first real vendor adapters:

- `ClaudeAgentRuntime` with lazy `claude-agent-sdk` imports and injected SDK
  tests.
- `CodexAgentRuntime` with lazy `openai-codex` imports and injected SDK tests.
- Adapter registry helper.
- Provider diagnostics helper.
- Quickstart and provider diagnostics docs.

Default tests remain credential-free and do not require vendor packages.
