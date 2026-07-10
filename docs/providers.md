# Provider Diagnostics

`agent-runtime-kit` separates cheap package discovery from provider setup
probes. Each runtime's synchronous `availability()` is side-effect-free and
package-only. It returns a `RuntimeAvailability` value with:

- runtime kind
- availability flag
- reason
- message
- package name
- installed version when discoverable

Availability never imports a vendor SDK, starts a subprocess, reads a
credential store, calls Google ADC, or contacts a provider. Package presence is
not a claim that execution will work.

Use the public `await check_readiness(runtime, timeout=5.0)` for an explicit
credential/setup probe. Registry and hub forms are also available:

```python
readiness = await registry.readiness_for("codex")
readiness = await kit.readiness_for("codex")
all_readiness = await kit.readiness()
```

`RuntimeReadiness.status` has three outcomes:

- `READY_TO_ATTEMPT`: a supported setup or credential signal was positively
  established. It does not promise that a later model/network request succeeds.
- `NOT_READY`: a concrete problem such as a missing package, account, API key,
  or ADC project was established.
- `INDETERMINATE`: the runtime uses a provider/local credential chain that
  cannot be verified safely, lacks the optional readiness extension, or the
  bounded probe failed/timed out. The caller chooses whether to attempt work.

The built-in probes never return credential values or raw exception messages.
They report only safe categories such as auth source and exception type. The
optional `RuntimeReadinessProvider` protocol does not change the required
`AgentRuntime` protocol. For older third-party runtimes,
`check_readiness()` maps negative availability to `NOT_READY` and positive
package presence conservatively to `INDETERMINATE`.

Provider behavior is deliberately specific:

- Claude treats direct API key, OAuth-token, and Bedrock bearer-token signals
  as `READY_TO_ATTEMPT`. Provider chains and provider-owned local login remain
  `INDETERMINATE`; probing them would require starting a real task or scraping
  unsupported credential stores.
- Codex starts the supported `AsyncCodex` client, calls
  `account(refresh_token=False)`, and always closes the app-server context. An
  account is `READY_TO_ATTEMPT`, no account is `NOT_READY`, and startup/API/
  cleanup failures are `INDETERMINATE`.
- Antigravity accepts an explicit or ambient Gemini API key, otherwise probes
  Google Application Default Credentials and project setup in a worker thread
  so synchronous Google discovery cannot block the event loop. Missing setup
  is `NOT_READY`; errors and timeouts are `INDETERMINATE`.

For all built-ins at once, the existing sync collector stays package-only while
the async collector performs bounded readiness checks:

```python
from agent_runtime_kit.adapters.diagnostics import (
    collect_provider_diagnostics,
    collect_provider_readiness,
)

packages = collect_provider_diagnostics()
readiness = await collect_provider_readiness(timeout=5.0)
```

## Install Model

The plain `agent-runtime-kit` package is the vendor-SDK-free core and includes
lightweight JSON Schema validation. Use
`agent-runtime-kit[all]` when you want Claude, Codex, and Antigravity adapters
available in one install, or use a single extra such as
`agent-runtime-kit[codex]` when your application only needs one runtime.

Provider extras are used to isolate packaging risk, not to fragment the API.
They avoid mandatory installs of every vendor SDK, CLI binary, and compiled
runtime wheel; keep unrelated providers working when one SDK changes or is not
available on a platform; and let runtime availability diagnostics point users
to the exact missing extra.

## Runtime Notes

All three adapters map the task's system prompt (Claude `system_prompt`, Codex
`developer_instructions`, Antigravity `system_instructions`) and the `model`
field. Model precedence is `AgentTask.model` > legacy `metadata["model"]` > the
adapter's `default_model=` constructor override > provider-native selection. At
the final step the adapter omits the SDK model option; it does not impose a
library-owned model that can go stale. Results always include
`metadata["model_source"]` (`task`, `metadata`, `constructor`, or
`provider-native`) and include `metadata["model"]` only when the selected value
is known. When `supported_models=` is configured, provider-native selection is
rejected as unverifiable until the caller chooses an explicit model.
`reasoning_effort` maps to the Claude and Codex `effort` options; Antigravity
has no reasoning-effort control and rejects the first-class field with a typed
error. Its legacy `metadata["reasoning_effort"]` alias is rejected too, rather
than being silently ignored. Effort values are passed through to the vendor SDK rather
than validated by this library — each vendor defines its own accepted
vocabulary (for example `claude-agent-sdk` 0.2.x accepts
`low`/`medium`/`high`/`xhigh`/`max`), and an SDK too old to accept `effort` at
all records the drop in `AgentResult.metadata["dropped_options"]`.

Usage fields are `None` when a provider omits its usage breakdown or cost.
Provider-reported zeros remain numeric zero, so callers can distinguish a free
or zero-token run from missing telemetry.

Every built-in adapter exposes a pure `validate_task()` extension. The public
`validate_task(runtime, task)`, registry, and `AgentKit` helpers call that richer
validator when present and otherwise fall back to declared capabilities for
older third-party runtimes. A report is a static preflight, not an availability
or credential probe; installed-SDK drift can still make dispatch fail closed.

Claude uses the `claude-agent-sdk` package and maps working directory,
permissions, filesystem access (a `READ_ONLY` filesystem forces `plan` mode),
MCP servers, sessions, structured output, tool allow/deny lists, runtime
environment, and budget (a requested `budget_usd` fails closed with a typed
error if the installed SDK stops accepting `max_budget_usd`, so a spend cap
can never silently vanish). It streams
incremental output and tool events while the SDK runs, and sets
`finish_reason="max_turns"` when a turn is truncated by the max-turns limit.
The effective vendor `permission_mode` is reported in
`AgentResult.metadata["permission_mode"]`.
`permissions.network` has no SDK surface and is rejected with a typed error.
Claude auth is provider-owned: use Anthropic API key auth, or configure
third-party provider modes through Claude Code environment/settings such as
`CLAUDE_CODE_USE_BEDROCK=1` with the AWS SDK credential chain,
`CLAUDE_CODE_USE_VERTEX=1` with Google credentials,
`CLAUDE_CODE_USE_ANTHROPIC_AWS=1`, or `CLAUDE_CODE_USE_FOUNDRY=1`.
`ClaudeAgentRuntime(env=...)` passes those provider-specific environment values
to the SDK subprocess without scraping credential files. Any option kwargs
dropped because a future SDK renamed or removed them are recorded in
`AgentResult.metadata["dropped_options"]` rather than discarded silently.
Permission-critical options are exempt from that tolerance and fail closed: if
the installed SDK cannot accept Claude's `permission_mode` (or a requested tool
allow/deny list), Codex's `sandbox`/`approval_mode`, or Antigravity's
`capabilities`/`policies`/workspace scoping, the run raises
`UnsupportedTaskInputError` instead of proceeding with weaker permissions than
the task requested. By
default each `run()` uses the one-shot SDK `query()` path. Long-lived callers can
pass `ClaudeAgentRuntime(reuse_process=True)` to keep a compatible
`ClaudeSDKClient` process open across tasks, then call `await runtime.aclose()`
or use `async with ClaudeAgentRuntime(..., reuse_process=True) as runtime:`.
Each task still uses an explicit stream session id, so omitted session handles do
not silently share Claude's default stream session. The reuse cache key is scoped
by conversation identity (`resume_from`/`session_id`): two tasks with different
sessions never share one client even when their options are identical, while tasks
without an explicit conversation share the process and stay isolated by their
per-query session id. `AgentResult.metadata["sdk_process_reuse_scope"]` reports
`"conversation"` or `"shared"` accordingly. The runtime restarts the SDK process
when the option fingerprint changes and evicts it after SDK exceptions.

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
`CLOUD_ML_REGION` is set. Precedence: an explicit constructor `api_key` is the
most specific request and wins; otherwise an explicit
`AntigravityAgentRuntime(vertex=True, ...)` takes precedence over an ambient
`GEMINI_API_KEY`/`GOOGLE_API_KEY`, so a Vertex-configured runtime is never
silently redirected to the Gemini API by an exported environment key. Under
`PERMISSIVE`, `disallowed_tools` maps to `CapabilitiesConfig.disabled_tools`
(the baseline is every tool, so "enable everything else" is exact); under any
other posture the deny-list is folded into an allow-list of the mode's baseline
minus the denied tools, so denying one tool can never re-enable others past the
baseline. An allow-list and a deny-list are mutually exclusive (the SDK forbids
combining enabled and disabled tool lists), so supplying both is rejected. Tool
names are validated against the
`BuiltinTools` enum (`"view_file"`, not `"Read"`). `budget_usd` and
`permissions.network` are rejected with typed errors, and MCP server configs do
not accept per-server env values. The default tool posture with no
`allowed_tools` is:

| `PermissionMode` / filesystem | Toolset | Policy |
|-------------------------------|---------|--------|
| `STRICT` (any filesystem) | read-only | none (no `allow_all`) |
| any mode with `READ_ONLY` filesystem | read-only | `allow_all` (policy follows the mode; only `STRICT` drops it) |
| `CAUTIOUS`, `DEFAULT` (writable filesystem) | nondestructive (no `run_command`) | `allow_all` |
| `PERMISSIVE` (writable filesystem) | all tools | `allow_all` |

A `READ_ONLY` filesystem forces the read-only toolset; the `allow_all` policy is
dropped only for `STRICT`. When an explicit `allowed_tools` list is combined with
a `READ_ONLY` filesystem, any non-read-only tool in it is rejected rather than
silently granted.

A deny-list under a `READ_ONLY` filesystem (or `STRICT`) subtracts from the
read-only toolset, and under `DEFAULT`/`CAUTIOUS` from the nondestructive
toolset — `disabled_tools` alone would re-enable every unnamed write or
destructive tool. An explicit `allowed_tools` list is rejected if it names a
non-read-only tool under a `READ_ONLY` filesystem or `STRICT` mode. An
`allowed_tools` list containing `start_subagent` enables subagents in any mode;
the approval policy still follows the mode (only `STRICT` omits `allow_all`).

Session and app-data directories are written under
`$XDG_CACHE_HOME/agent-runtime-kit` (default `~/.cache/agent-runtime-kit`,
`0o700`), overridable via `AntigravityAgentRuntime(data_dir=...)`. That
placement depends on the installed SDK accepting the `save_dir`/`app_data_dir`
kwargs; if a future SDK renames them the drop is recorded in
`dropped_options` and transcripts land in the SDK's own default location —
data placement, unlike the tool posture, is tolerated rather than failed
closed. By default
each `run()` uses a fresh `Agent` context. Long-lived callers can pass
`AntigravityAgentRuntime(reuse_process=True)` and close it with
`await runtime.aclose()` or an async context manager. The Antigravity SDK ties
the local agent process to a conversation, so the adapter reuses the process
only for tasks that provide an explicit `session_id` or `resume_from` handle.
Tasks without an explicit conversation id remain task-isolated and restart the
agent even when process reuse is enabled.

Across all three adapters, `reuse_process=True` serializes `run()` calls on that
runtime instance: because a single reused vendor subprocess is shared, concurrent
runs on the same instance are run one at a time. This is a deliberate trade-off of
the shared-process mode — use separate runtime instances (the default per-call
isolation) when you need concurrent execution.
