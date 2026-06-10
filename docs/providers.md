# Provider Diagnostics

`agent-runtime-kit` keeps provider setup checks explicit. Each runtime exposes
`availability()` and returns a `RuntimeAvailability` value with:

- runtime kind
- availability flag
- reason
- message
- package name
- installed version when discoverable

Claude uses the `claude-agent-sdk` package and maps working directory,
permissions, MCP servers, sessions, structured output, tool allow/deny lists,
and budget where supported by the installed SDK.

Codex uses the `openai-codex` package and maps working directory, session
resume, approval mode, sandbox, structured output, model, and reasoning effort.
Codex does not currently support per-task MCP server configuration through this
adapter, so `mcp_servers` is rejected with a typed error.
