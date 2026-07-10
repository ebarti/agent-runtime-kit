# Capability Matrix

| Capability | Claude Agent SDK | OpenAI Codex SDK | Google Antigravity SDK |
|------------|------------------|------------------|------------------------|
| Optional extra | `claude` | `codex` | `antigravity` |
| Core import without extra | Yes | Yes | Yes |
| Working directory | Yes | Yes | Yes |
| Session resume | Yes | Yes | Yes |
| Structured output | Native `output_format` when available | Native output schema / JSON parse fallback | Native response schema / JSON parse fallback |
| MCP stdio servers | Yes | No per-task MCP config | Yes, without per-server env |
| Permission mapping | `permission_mode` | approval mode + sandbox | capabilities + policies |
| Streaming output events | Yes — incremental `output.delta` while the SDK runs | No — a single `output.delta` at the end (non-streaming SDK) | Yes — from response chunks |
| Tool audit events | Yes — streamed from message blocks | Yes — emitted from parsed `TurnResult` items after the turn | Yes — from tool chunks |
| `vendor.turn` events | No | No | Yes — from thought/unknown chunks |
| Missing package diagnostics | Yes (`AgentRuntimeUnavailableError`) | Yes (`AgentRuntimeUnavailableError`) | Yes (`AgentRuntimeUnavailableError`) |
| Missing credential diagnostics | No — `availability()` reports available with an `auth_source` label; auth failures surface at `run()` | No — same as Claude (`auth_source` label; deferred) | Yes — `availability()` returns `MISSING_CREDENTIALS` when no API key / ADC-Vertex project is configured |
| Live smoke test | Opt-in | Opt-in | Opt-in |

The matrix is intentionally not a lowest-common-denominator contract. Adapters
reject unsupported inputs (see below) when silently dropping them would be
misleading.

For every provider, malformed schemas are rejected locally before dispatch and
returned structured values are validated locally after the SDK completes.
`parsed_output_available` distinguishes valid JSON `null` from no parsed value.

## Permission mapping

A single portable `PermissionMode` maps to each vendor's native controls. The
same profile yields different but intentionally aligned postures per runtime;
review this table before assuming a mode is equivalent everywhere.

| `PermissionMode` | Claude `permission_mode` | Codex `approval_mode` + sandbox | Antigravity toolset + policy |
|------------------|--------------------------|---------------------------------|------------------------------|
| `STRICT` | `plan` | `deny_all` (never escalate) | read-only toolset, no `allow_all` policy |
| `CAUTIOUS` | `default` | `deny_all` (never escalate) | nondestructive toolset (no `run_command`), `allow_all` policy |
| `DEFAULT` | `default` | `auto_review` (escalations auto-adjudicated) | nondestructive toolset (no `run_command`), `allow_all` policy |
| `PERMISSIVE` | `bypassPermissions` | `auto_review` (escalations auto-adjudicated) | all tools, `allow_all` policy |

The ladder is monotonic: `CAUTIOUS` is never looser than `DEFAULT` on any
backend. Claude has no distinct cautious-execution tier, so `CAUTIOUS` and
`DEFAULT` both map to `default` (no auto-approval); `acceptEdits` is not used, as
it would auto-approve edits and in-cwd deletes. Claude's effective
`permission_mode` is echoed in `AgentResult.metadata["permission_mode"]`.

Codex sandbox is derived from `FilesystemAccess`, independent of the approval
mode: `READ_ONLY` → `read_only`, `WORKSPACE_WRITE` → `workspace_write`,
`FULL_ACCESS` → `full_access`. On Claude, a `READ_ONLY` filesystem forces
`plan` mode (no writes) regardless of `PermissionMode`.

Antigravity toolset notes: the table above describes the default posture when
`allowed_tools` is empty. A `READ_ONLY` filesystem forces the read-only toolset
regardless of mode. User-supplied `allowed_tools`/`disallowed_tools` are
validated against the `BuiltinTools` enum (for example `"view_file"`, not
`"Read"`); an unknown name raises `UnsupportedTaskInputError`. An allow-list
naming a non-read-only tool under a `READ_ONLY` filesystem or `STRICT` mode is
rejected, and a deny-list subtracts from the mode's baseline toolset rather
than re-enabling everything else (only `PERMISSIVE`, whose baseline is every
tool, uses the SDK's `disabled_tools` route).

## Rejected inputs

Each adapter raises `UnsupportedTaskInputError` for task fields it has no SDK
surface to honor, rather than dropping them silently.

| Field | Claude | Codex | Antigravity |
|-------|--------|-------|-------------|
| `budget_usd` | Mapped (`max_budget_usd`, fails closed under SDK drift) | Rejected | Rejected |
| `reasoning_effort` | Mapped (`effort`) | Mapped (`effort`) | Rejected (no SDK surface) |
| `permissions.network` | Rejected | Rejected | Rejected |
| `allowed_tools` / `disallowed_tools` | Mapped | Rejected | Mapped; deny-lists subtract from the mode's baseline (only `PERMISSIVE` uses `disabled_tools`), and allow-list plus deny-list together is rejected |
| `mcp_servers` | Mapped | Rejected (no per-task MCP) | Mapped, without per-server `env` |

Two task fields are informational only and not enforced by any built-in adapter:
`AgentTask.sdk_executions` (carried into events as a hint) and
`SessionResumeState.transcript` (adapters resume by `session_id`).

Claude additionally records any vendor-option kwargs it had to drop due to SDK
drift in `AgentResult.metadata["dropped_options"]`, so silent omission stays
observable. Permission-critical options are exempt from that drift tolerance:
when the installed SDK cannot accept them, the run fails with a typed error
rather than running with weaker permissions than requested.

## Session storage (Antigravity)

Antigravity session and app-data directories are written under
`$XDG_CACHE_HOME/agent-runtime-kit` (default `~/.cache/agent-runtime-kit`),
created with `0o700` permissions. This replaces the previous world-shared
`/tmp` location so transcripts survive reboots and are not exposed to other
users. Override the base directory with
`AntigravityAgentRuntime(data_dir=...)`.
