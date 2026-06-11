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
| Streaming output events | Yes — incremental output/tool events while the SDK runs | Not enabled in v1 adapter | Yes — from response chunks |
| Tool audit events | Yes — from streamed message blocks | Yes — parsed from `TurnResult` items | Yes — from tool chunks |
| Missing package diagnostics | Yes (`AgentRuntimeUnavailableError`) | Yes (`AgentRuntimeUnavailableError`) | Yes (`AgentRuntimeUnavailableError`) |
| Missing credential diagnostics | Provider-owned/local auth | Provider-owned/local auth | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Live smoke test | Opt-in | Opt-in | Opt-in |

The matrix is intentionally not a lowest-common-denominator contract. Adapters
reject unsupported inputs (see below) when silently dropping them would be
misleading.

## Permission mapping

A single portable `PermissionMode` maps to each vendor's native controls. The
same profile yields different but intentionally aligned postures per runtime;
review this table before assuming a mode is equivalent everywhere.

| `PermissionMode` | Claude `permission_mode` | Codex `approval_mode` + sandbox | Antigravity toolset + policy |
|------------------|--------------------------|---------------------------------|------------------------------|
| `STRICT` | `plan` | `deny_all` (never escalate) | read-only toolset, no `allow_all` policy |
| `CAUTIOUS` | `acceptEdits` | `deny_all` (never escalate) | nondestructive toolset (no `run_command`), `allow_all` policy |
| `DEFAULT` | `default` | `auto_review` (escalations auto-adjudicated) | nondestructive toolset (no `run_command`), `allow_all` policy |
| `PERMISSIVE` | `bypassPermissions` | `auto_review` (escalations auto-adjudicated) | all tools, `allow_all` policy |

Codex sandbox is derived from `FilesystemAccess`, independent of the approval
mode: `READ_ONLY` → `read_only`, `WORKSPACE_WRITE` → `workspace_write`,
`FULL_ACCESS` → `full_access`.

Antigravity toolset notes: the table above describes the default posture when
`allowed_tools` is empty. A `READ_ONLY` filesystem forces the read-only toolset
regardless of mode. User-supplied `allowed_tools`/`disallowed_tools` override the
defaults and are validated against the `BuiltinTools` enum (for example
`"view_file"`, not `"Read"`); an unknown name raises `UnsupportedTaskInputError`.

## Rejected inputs

Each adapter raises `UnsupportedTaskInputError` for task fields it has no SDK
surface to honor, rather than dropping them silently.

| Field | Claude | Codex | Antigravity |
|-------|--------|-------|-------------|
| `budget_usd` | Mapped (`max_budget_usd`) | Rejected | Rejected |
| `permissions.network` | Rejected | Rejected | Rejected |
| `allowed_tools` / `disallowed_tools` | Mapped | Rejected | Mapped (`disallowed_tools` → `disabled_tools`); allow-list and deny-list are mutually exclusive and rejected if combined |
| `mcp_servers` | Mapped | Rejected (no per-task MCP) | Mapped, without per-server `env` |

Two task fields are informational only and not enforced by any built-in adapter:
`AgentTask.sdk_executions` (carried into events as a hint) and
`SessionResumeState.transcript` (adapters resume by `session_id`).

Claude additionally records any vendor-option kwargs it had to drop due to SDK
drift in `AgentResult.metadata["dropped_options"]`, so silent omission stays
observable.

## Session storage (Antigravity)

Antigravity session and app-data directories are written under
`$XDG_CACHE_HOME/agent-runtime-kit` (default `~/.cache/agent-runtime-kit`),
created with `0o700` permissions. This replaces the previous world-shared
`/tmp` location so transcripts survive reboots and are not exposed to other
users. Override the base directory with
`AntigravityAgentRuntime(data_dir=...)`.
