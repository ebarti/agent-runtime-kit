# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Core JSON Schema validation via `jsonschema`, including construction-time
  schema checks and `OutputSchemaError` for malformed definitions.
- `AgentResult.parsed_output_available` distinguishes a valid JSON `null` from
  absent or rejected structured output.
- All built-in adapters and public fake runtimes now validate returned values
  against `output_schema` locally, including textual JSON fallbacks.
- `AgentResult.is_success` provides one canonical success predicate.
- `TaskSupportReport`, `TaskSupportProvider`, and `validate_task()` provide
  side-effect-free task preflight without adding a requirement to the
  `AgentRuntime` protocol. Registry and `AgentKit` helpers preserve custom
  runtime validation and capability-based fallback for older runtimes.
- `COMPATIBILITY_MANIFEST` records each built-in adapter's install extra,
  import module, accepted SDK range, tested lockfile version, and runtime binary
  versions such as `openai-codex-cli-bin`.

### Changed

- Pydantic structured-output parsing now requests strict validation, so values
  such as `"42"` no longer coerce into integer fields.
- BREAKING: task and value-object constructors now reject blank identities,
  non-positive execution hints, invalid budgets, ambiguous session inputs,
  duplicate MCP names, and conflicting tool filters.
- BREAKING: unknown `Usage` values, including cost, are `None` rather than zero;
  an explicit provider-reported zero remains `0`/`0.0`.
- Capability declarations now distinguish budget, reasoning-effort, network,
  tool-filter, and per-MCP-server environment support. Built-in `run()` methods
  consume the same support report used by preflight, eliminating divergent
  rejection paths.

### Fixed

- `AgentKit.run(task=...)` now rejects every explicitly supplied per-field task
  argument, including default-valued `sdk_executions=1` and empty tool lists,
  instead of silently ignoring it.
- `ParsedResult[T]` clears an adapter's raw `parsed_output` when the adapter
  reports failure, so the typed result surface never exposes unvalidated data.
- `AgentKit.aclose()` attempts to close every cached runtime before reraising
  the first close error, preventing later resources from leaking.
- Permission- and budget-critical vendor options must now be explicit,
  introspectable SDK parameters; opaque `**kwargs`, positional-only options,
  and uninspectable callables fail closed.
- Configured model allow-lists, Antigravity MCP name syntax, disjoint
  allow/deny lists, and Antigravity's legacy reasoning-effort alias are rejected
  during static preflight instead of surfacing later or being silently ignored.

## 0.4.0 - 2026-07-02

### Added

- `AgentKit`, a FastAPI-flavored hub over the registry: keyword-native
  `await kit.run("claude", goal=..., permissions="strict", ...)` assembling
  the frozen `AgentTask` internally (a prebuilt `task=` still passes through),
  short aliases for the built-in kinds via the documented `KIND_ALIASES`
  mapping, per-kind runtime caching closed by `aclose()`/`async with`,
  `@kit.on(...)` sync/async event handlers that tee alongside a task's own
  sink and can never break a run, and `@kit.runtime(...)` decorator
  registration for third-party kinds.
- Typed structured output: `kit.run(..., output_type=SomeType)` derives the
  wire schema from a dataclass/`TypedDict` (dependency-free, bounded subset,
  fail-closed via the new `OutputTypeError`) or from a Pydantic-style model's
  own `model_json_schema`, and validates `parsed_output` back into the type.
  The result is `ParsedResult[T]` — a runtime-identical `AgentResult`
  subclass with a typed `parsed` accessor; `AgentResult` itself stays
  non-generic. Non-conforming payloads yield `finish_reason="failed"`, the
  adapters' own structured-output convention.
- `PermissionProfile` accepts string literals for `mode`/`filesystem`
  ("strict", "read-only", ...) and coerces them to real enum members at
  construction; unknown values raise `ValueError` listing the vocabulary.
  Previously a bare string silently matched none of the adapters' identity
  checks and ran at the default posture.

## 0.3.0 - 2026-07-02

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
  `AgentTask.model` and `AgentTask.reasoning_effort` fields (keyword-only, so
  the positional layout that predates them is unchanged; the
  `metadata["model"]`/`metadata["reasoning_effort"]` aliases keep working).
  `model` is honored by all three adapters. `reasoning_effort` maps to the
  Claude and Codex `effort` options; Antigravity has no reasoning-effort
  control and rejects the field with a typed error instead of silently
  ignoring it.
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
  copied at construction and read-only at the top level afterwards (in-place
  mutation of the mapping itself raises `TypeError`; the freeze is shallow, so
  nested containers are not copied or frozen); `dataclasses.asdict`,
  `copy.deepcopy`, `pickle`, and JSON serialization keep working.
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
  combining a `READ_ONLY` filesystem or `STRICT` mode with non-read-only
  `allowed_tools` is rejected instead of silently granted, and vendor stop
  reasons map to `max_tokens`/`failed` finish reasons.
- BREAKING: Antigravity deny-lists are now subtractive in every mode.
  `disabled_tools` means "enable everything else", which re-enabled write and
  destructive tools past the mode's baseline; now only `PERMISSIVE` (whose
  baseline is every tool) still takes that route, while `READ_ONLY`/`STRICT`
  get the read-only toolset minus the denied tools and `DEFAULT`/`CAUTIOUS`
  the nondestructive toolset minus the denied tools.
- Vendor SDK dependencies now carry pre-1.0 upper bounds (`claude-agent-sdk<0.3`,
  `openai-codex<0.2`, `google-antigravity<0.2`) so a breaking upstream minor
  cannot reach fresh installs before adapters are revalidated.
- BREAKING: permission-critical SDK options fail closed under vendor drift. If
  the installed SDK cannot accept Claude's `permission_mode` (or a requested
  tool allow/deny list), Codex's `sandbox`/`approval_mode`, or Antigravity's
  `capabilities`/`policies`/workspace scoping, `run()` raises
  `UnsupportedTaskInputError` instead of silently running under the SDK's
  default (more permissive) posture. A requested `budget_usd` fails closed the
  same way if the installed Claude SDK stops accepting `max_budget_usd`, so a
  spend cap can never silently vanish. Non-security drift is still tolerated
  and recorded in `AgentResult.metadata["dropped_options"]`.

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
- Codex: a turn ending in the SDK's non-terminal `inProgress` status — or any
  future unknown status — now fails closed as `finish_reason="failed"` instead
  of reading as success with partial output.
- Codex: `Usage.input_tokens` now excludes cached input tokens, which are
  reported separately in `cache_read_tokens` (matching the documented `Usage`
  contract and the Antigravity adapter) instead of being double-counted across
  both fields.
- Event redaction now covers camelCase secret keys (e.g. `accessToken`) and
  separator-less ones (e.g. `accesstoken`, `SESSIONTOKEN`) while keeping plural
  usage counters (`inputTokens`, `totaltokens`) visible, and the event
  sanitizer bounds recursion depth and detects reference cycles so pathological
  metadata cannot abort a run.
- SDK evolution example: inspecting candidate SDK versions (which pip-installs
  and imports freshly downloaded upstream code) is now opt-in via
  `--inspect-candidates` and runs in a credential-scrubbed environment — and
  the behavior probes now honor the same gate and scrub instead of installing
  candidates unconditionally with the caller's full environment (skipped
  candidates are recorded explicitly). `--draft-pr` no longer fails when the
  report directory is gitignored.
- SDK evolution example: release-note links extracted from fetched pages are
  only followed when they resolve to `https://github.com` (a protocol-relative
  href in user-generated discussion markup could otherwise trigger an off-site
  request), and a configured-but-failing GitHub GraphQL token now surfaces on
  the source instead of silently downgrading to the unauthenticated scrape.

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
