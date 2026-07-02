# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Codex runtimes can now opt into reusable SDK process lifecycle with
  `CodexAgentRuntime(reuse_process=True)`, `await runtime.aclose()`, or
  `async with CodexAgentRuntime(..., reuse_process=True)`. Reuse keeps the Codex
  app-server process alive across compatible tasks without implicitly reusing
  Codex threads or sessions.
- Claude and Antigravity runtimes can now opt into adapter-owned SDK process
  lifecycle with the same `reuse_process=True`, `aclose()`, and async context
  manager shape. Claude reuses `ClaudeSDKClient` while preserving per-task
  stream session IDs; Antigravity only reuses for explicit conversation IDs
  because its SDK process is conversation-scoped.
- The SDK evolution agent now enables vendor process reuse for multi-stage
  SDK-backed runs and closes internally owned runtimes when the run exits.
- `FinishReason` enum of the canonical `finish_reason` values, plus first-class
  `AgentTask.model` and `AgentTask.reasoning_effort` fields (the
  `metadata["model"]`/`metadata["reasoning_effort"]` aliases keep working).
- Third-party runtime kinds: `AgentRuntimeKind.coerce` and the registry accept
  namespaced strings (e.g. `"x-myorg-agent"`), and `runtime_kind_value()`
  returns the wire form of either shape.
- Tool observability parity: the Codex adapter emits `agent.tool.requested` /
  `agent.tool.completed` events, Claude tool audits carry `result_preview`, and
  Antigravity audits are recorded per call rather than collapsed by tool name.
- Claude reports the effective vendor permission mode in
  `AgentResult.metadata["permission_mode"]`, and the Codex/Antigravity adapters
  record SDK kwargs dropped for compatibility in
  `AgentResult.metadata["dropped_options"]`.
- `docs/api-stability.md` documents the public surface, the 0.x compatibility
  policy, and the vendor SDK pinning policy.

### Changed

- BREAKING: the `AgentRuntime` protocol now requires `aclose()` and async
  context-manager support, and `kind` is typed `AgentRuntimeKind | str` so
  third-party runtimes can conform without forking the enum.
- BREAKING: mapping fields on `AgentTask`, `AgentResult`, and related models are
  copied at construction and read-only afterwards (in-place mutation raises
  `TypeError`); `dataclasses.asdict`, `copy.deepcopy`, `pickle`, and JSON
  serialization keep working.
- Claude: `CAUTIOUS` now maps to the vendor `default` permission mode instead of
  `acceptEdits` (it was looser than `DEFAULT`), and a `READ_ONLY` filesystem
  forces `plan` mode.
- Claude: an unsatisfied `output_schema` or an empty completion now returns
  `finish_reason="failed"` instead of silent success.
- Codex: `Usage` reports the executed turn rather than cumulative
  across-the-thread totals when resuming sessions.
- All adapters: `AgentResult.session_id` falls back to the task-supplied session
  handle when the SDK response omits one.
- Antigravity: explicit `vertex=True` takes precedence over ambient API keys,
  combining a `READ_ONLY` filesystem with non-read-only `allowed_tools` is
  rejected instead of silently granted, and vendor stop reasons map to
  `max_tokens`/`failed` finish reasons.
- Vendor SDK dependencies now carry pre-1.0 upper bounds (`claude-agent-sdk<0.3`,
  `openai-codex<0.2`, `google-antigravity<0.2`) so a breaking upstream minor
  cannot reach fresh installs before adapters are revalidated.
- BREAKING: permission-critical SDK options fail closed under vendor drift. If
  the installed SDK cannot accept Claude's `permission_mode` (or a requested
  tool allow/deny list), Codex's `sandbox`/`approval_mode`, or Antigravity's
  `capabilities`/`policies`/workspace scoping, `run()` raises
  `UnsupportedTaskInputError` instead of silently running under the SDK's
  default (more permissive) posture. Non-security drift is still tolerated and
  recorded in `AgentResult.metadata["dropped_options"]`.

- Installation docs now lead with `agent-runtime-kit[all]` for the easiest
  full-provider setup and explain provider extras as dependency isolation, not a
  separate API.
- README and package metadata now include clearer About copy and searchable
  tags for agent runtime, coding-agent, Claude Code, Codex, Antigravity, MCP,
  typed Python, and async Python use cases.
- The source distribution now allowlists individual public docs instead of the
  whole `docs/` directory.

### Removed

- Removed the stale internal publish checklist from public documentation.
- BREAKING: removed the dead `AgentCapabilities.sdk_turn_limit` field (no
  adapter ever read it).

### Fixed

- Reused vendor SDK clients are evicted when a run is interrupted or cancelled
  mid-flight, and `aclose()` no longer races an in-flight run on the same
  runtime instance.
- Event redaction now covers camelCase secret keys (e.g. `accessToken`), and
  the event sanitizer bounds recursion depth and detects reference cycles so
  pathological metadata cannot abort a run.
- SDK evolution example: inspecting candidate SDK versions (which pip-installs
  and imports freshly downloaded upstream code) is now opt-in via
  `--inspect-candidates` and runs in a credential-scrubbed environment, and
  `--draft-pr` no longer fails when the report directory is gitignored.

## 0.2.0 - 2026-06-23

### Added

- Added the local SDK evolution agent example, including runtime-backed
  direction analysis, architecture decision, reviewer stages, report generation,
  and optional draft PR creation.
- Added release-note collection, candidate API snapshots/diffs, behavior probes,
  current-state promotion manifests, and deterministic implementation gates for
  SDK update runs.
- Added documentation for running the SDK evolution agent locally with supported
  vendor credentials and reviewing generated evidence.

### Changed

- Updated the lockfile to the resolver-selected current Claude and Antigravity
  SDK versions after the evolution agent validated adapter-contract behavior:
  `claude-agent-sdk` 0.2.106 and `google-antigravity` 0.1.4.
- SDK evolution current-state manifests now record repo-relative checked-in
  artifact paths rather than machine-local absolute paths.

## 0.1.1 - 2026-06-11

### Fixed

- **Codex approval-mode mapping was inverted** and is now corrected. `STRICT` and
  `CAUTIOUS` map to `deny_all` (never escalate beyond the sandbox); `DEFAULT` and
  `PERMISSIVE` map to `auto_review` (escalations are auto-adjudicated). Previously a
  `STRICT` caller could have escalations auto-approved and a `PERMISSIVE` caller had
  them hard-denied — exactly backwards.
- Antigravity MCP stdio server configuration no longer crashes: the required `name`
  field is now passed to the SDK model.
- The `mypy` strict gate now passes identically in core-only and all-extras
  environments (per-module import overrides replace inline ignores).
- Claude reports `finish_reason="max_turns"` when a turn is truncated by the
  max-turns limit instead of a generic failure.
- Codex honors `TurnResult.status`: a `failed` or `interrupted` turn maps to the
  matching `finish_reason` and surfaces the structured error message instead of being
  reported as success.

### Changed

- **Antigravity DEFAULT permission posture is now safer.** With no `allowed_tools`,
  `DEFAULT` (and `CAUTIOUS`) now use the nondestructive toolset (no `run_command`)
  instead of granting all tools plus an allow-all policy. `STRICT` (or any read-only
  filesystem) uses the read-only toolset with no `allow_all` policy; `PERMISSIVE`
  keeps all tools.
- **Unsupported task fields now raise `UnsupportedTaskInputError` instead of being
  silently ignored.** Codex rejects `allowed_tools`, `disallowed_tools`, `budget_usd`,
  and `permissions.network` (MCP was already rejected); Antigravity rejects
  `budget_usd` and `permissions.network`, and rejects combining an allow-list with a
  deny-list (the SDK requires them to be mutually exclusive); Claude rejects
  `permissions.network`. Only Claude maps `budget_usd`.
- Missing-SDK and missing-credential paths now raise the typed
  `AgentRuntimeUnavailableError` instead of a bare `RuntimeError`.
- Antigravity session and app-data storage moved from a world-shared
  `/tmp/agent-runtime-kit` path to `$XDG_CACHE_HOME/agent-runtime-kit` (default
  `~/.cache/agent-runtime-kit`, created `0o700`), overridable via
  `AntigravityAgentRuntime(data_dir=...)`. Sessions now survive reboots and are not
  exposed to other users on multi-user machines.
- Antigravity maps `disallowed_tools` to `CapabilitiesConfig.disabled_tools` and
  validates tool names against the `BuiltinTools` enum, raising a typed error for an
  unknown name instead of leaking a raw validation error.
- Build now requires `hatchling>=1.26` (for the PEP 639 `license = "MIT"` string),
  and the deprecated `License :: OSI Approved :: MIT License` classifier was removed.

### Added

- Claude streaming is now real: incremental output deltas and
  `tool_requested`/`tool_completed` events are emitted while the SDK runs.
- Codex tool audits are now produced, parsed from `TurnResult.items` (command
  executions, MCP tool calls, dynamic tool calls, and web searches).
- Claude records vendor-option kwargs it had to drop due to SDK drift in
  `AgentResult.metadata["dropped_options"]`, keeping silent omissions observable.
- A continuous-integration workflow runs `ruff`, `mypy`, and `pytest` across
  Python 3.10–3.13 in both the core-only and all-extras dependency lanes, and the
  PyPI publish workflow now gates on a test job before building.
- `tests/test_sdk_contract.py` introspects the real vendor SDK surfaces and
  auto-skips when the SDKs are not installed.
- An sdist allowlist ensures only `src`, `tests`, `docs`, `examples`, `README.md`,
  `LICENSE`, and `pyproject.toml` are packaged for PyPI (internal planning
  artifacts, `uv.lock`, and caches are no longer shipped).

## 0.1.0

- Initial release: one typed async runtime API for the Claude, Codex, and
  Antigravity agent SDKs, with capability diagnostics, event sinks, and adapters.
