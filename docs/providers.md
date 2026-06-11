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
and budget where supported by the installed SDK. It streams incremental output
and tool events while the SDK runs, and sets `finish_reason="max_turns"` when a
turn is truncated by the max-turns limit. `permissions.network` has no SDK
surface and is rejected with a typed error. Any option kwargs dropped because a
future SDK renamed or removed them are recorded in
`AgentResult.metadata["dropped_options"]` rather than discarded silently.

Codex uses the `openai-codex` package and maps working directory, session
resume, approval mode, sandbox, structured output, model, and reasoning effort.
Approval mode follows `PermissionMode`: `STRICT`/`CAUTIOUS` → `deny_all` (never
escalate beyond the sandbox), `DEFAULT`/`PERMISSIVE` → `auto_review`
(escalations auto-adjudicated). Tool audits are parsed from `TurnResult.items`
(command executions, MCP tool calls, dynamic tool calls, web searches), and a
`TurnResult.status` of `failed`/`interrupted` maps to the matching
`finish_reason`. `budget_usd`, `permissions.network`, `allowed_tools`,
`disallowed_tools`, and `mcp_servers` have no per-task SDK surface and are
rejected with typed errors. The constructor defaults to
`config_overrides=("features.plugins=false",)` so headless runs are
deterministic and do not pick up host-local Codex plugin configuration; pass a
different tuple to opt in.

Antigravity uses the `google-antigravity` package and maps API key,
workspace, permission-derived capabilities/policies, MCP stdio servers,
conversation id, structured output, session directories, model, and tool
events. `disallowed_tools` maps to `CapabilitiesConfig.disabled_tools`, and an
allow-list and a deny-list are mutually exclusive (the SDK forbids combining
enabled and disabled tool lists), so supplying both is rejected. Tool names are
validated against the `BuiltinTools` enum (`"view_file"`, not `"Read"`).
`budget_usd` and `permissions.network` are rejected with typed errors, and
MCP server configs do not accept per-server env values. The default tool posture
with no `allowed_tools` is:

| `PermissionMode` (or `READ_ONLY` filesystem) | Toolset | Policy |
|----------------------------------------------|---------|--------|
| `STRICT`, or any `READ_ONLY` filesystem | read-only | none (no `allow_all`) |
| `CAUTIOUS`, `DEFAULT` | nondestructive (no `run_command`) | `allow_all` |
| `PERMISSIVE` | all tools | `allow_all` |

Session and app-data directories are written under
`$XDG_CACHE_HOME/agent-runtime-kit` (default `~/.cache/agent-runtime-kit`,
`0o700`), overridable via `AntigravityAgentRuntime(data_dir=...)`.
