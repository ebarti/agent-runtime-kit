# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
