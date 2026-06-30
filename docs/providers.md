# Provider Diagnostics

`agent-runtime-kit` keeps provider setup checks explicit. Each runtime exposes
`availability()` and returns a `RuntimeAvailability` value with:

- runtime kind
- availability flag
- reason
- message
- package name
- installed version when discoverable

## Install Model

The plain `agent-runtime-kit` package is the dependency-free core. Use
`agent-runtime-kit[all]` when you want Claude, Codex, and Antigravity adapters
available in one install, or use a single extra such as
`agent-runtime-kit[codex]` when your application only needs one runtime.

Provider extras are used to isolate packaging risk, not to fragment the API.
They avoid mandatory installs of every vendor SDK, CLI binary, and compiled
runtime wheel; keep unrelated providers working when one SDK changes or is not
available on a platform; and let runtime availability diagnostics point users
to the exact missing extra.

## Runtime Notes

Claude uses the `claude-agent-sdk` package and maps working directory,
permissions, MCP servers, sessions, structured output, tool allow/deny lists,
runtime environment, and budget where supported by the installed SDK. It
streams incremental output and tool events while the SDK runs, and sets
`finish_reason="max_turns"` when a turn is truncated by the max-turns limit.
`permissions.network` has no SDK surface and is rejected with a typed error.
Claude auth is provider-owned: use Anthropic API key auth, or configure
third-party provider modes through Claude Code environment/settings such as
`CLAUDE_CODE_USE_BEDROCK=1` with the AWS SDK credential chain,
`CLAUDE_CODE_USE_VERTEX=1` with Google credentials,
`CLAUDE_CODE_USE_ANTHROPIC_AWS=1`, or `CLAUDE_CODE_USE_FOUNDRY=1`.
`ClaudeAgentRuntime(env=...)` passes those provider-specific environment values
to the SDK subprocess without scraping credential files. Any option kwargs
dropped because a future SDK renamed or removed them are recorded in
`AgentResult.metadata["dropped_options"]` rather than discarded silently. By
default each `run()` uses the one-shot SDK `query()` path. Long-lived callers can
pass `ClaudeAgentRuntime(reuse_process=True)` to keep a compatible
`ClaudeSDKClient` process open across tasks, then call `await runtime.aclose()`
or use `async with ClaudeAgentRuntime(..., reuse_process=True) as runtime:`.
Each task still uses an explicit stream session id, so omitted session handles do
not silently share Claude's default stream session. The runtime restarts the SDK
process when the option fingerprint changes and evicts it after SDK exceptions.

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
different tuple to opt in. By default each `run()` uses a fresh Codex SDK
context. Long-lived callers that run several Codex tasks with the same runtime
can pass `CodexAgentRuntime(reuse_process=True)` to keep the Codex app-server
process open across compatible tasks, then call `await runtime.aclose()` or use
`async with CodexAgentRuntime(..., reuse_process=True) as runtime:`. This reuses
only the SDK process; each task still starts a fresh Codex thread unless the
caller supplies an explicit `session_id` or `resume_from` handle. The runtime
restarts the SDK process when cwd/model/env/config/permission fingerprints
change, and evicts it after SDK exceptions. Codex auth is owned by the local
Codex runtime: ChatGPT sign-in, API-key sign-in, access-token setup, and custom
providers stay in Codex config. For Amazon Bedrock, pass Codex config overrides
such as `model_provider=amazon-bedrock` and provider-specific model/profile/region
settings, and pass AWS environment values with `CodexAgentRuntime(env=...)` when
they should be scoped to the SDK subprocess.

Antigravity uses the `google-antigravity` package and maps API-key or Google
Application Default Credentials auth, workspace, permission-derived
capabilities/policies, MCP stdio servers, conversation id, structured output,
session directories, model, and tool events. API-key auth comes from the
constructor, `GEMINI_API_KEY`, or `GOOGLE_API_KEY`. Without an API key, the
adapter uses Vertex AI config with Google ADC when a project can be discovered
from ADC, `GOOGLE_CLOUD_PROJECT`, or `GCLOUD_PROJECT`; location defaults to
`global` unless `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_REGION`, or
`CLOUD_ML_REGION` is set. `disallowed_tools` maps to
`CapabilitiesConfig.disabled_tools`, and an allow-list and a deny-list are
mutually exclusive (the SDK forbids combining enabled and disabled tool lists),
so supplying both is rejected. Tool names are validated against the
`BuiltinTools` enum (`"view_file"`, not `"Read"`). `budget_usd` and
`permissions.network` are rejected with typed errors, and MCP server configs do
not accept per-server env values. The default tool posture with no
`allowed_tools` is:

| `PermissionMode` (or `READ_ONLY` filesystem) | Toolset | Policy |
|----------------------------------------------|---------|--------|
| `STRICT`, or any `READ_ONLY` filesystem | read-only | none (no `allow_all`) |
| `CAUTIOUS`, `DEFAULT` | nondestructive (no `run_command`) | `allow_all` |
| `PERMISSIVE` | all tools | `allow_all` |

Session and app-data directories are written under
`$XDG_CACHE_HOME/agent-runtime-kit` (default `~/.cache/agent-runtime-kit`,
`0o700`), overridable via `AntigravityAgentRuntime(data_dir=...)`. By default
each `run()` uses a fresh `Agent` context. Long-lived callers can pass
`AntigravityAgentRuntime(reuse_process=True)` and close it with
`await runtime.aclose()` or an async context manager. The Antigravity SDK ties
the local agent process to a conversation, so the adapter reuses the process
only for tasks that provide an explicit `session_id` or `resume_from` handle.
Tasks without an explicit conversation id remain task-isolated and restart the
agent even when process reuse is enabled.
