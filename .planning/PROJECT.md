# agent-runtime-kit

## What This Is

`agent-runtime-kit` is a published Python package ([PyPI](https://pypi.org/project/agent-runtime-kit/),
v0.1.1) that gives developers one clean API for running agentic coding tasks
through Claude Agent SDK, OpenAI Codex SDK, and Google Antigravity SDK. It
extracts the useful vendor-runtime ideas from Mestre while remaining
independently useful to the community: install it, choose a runtime, run a
task, stream/inspect events, and get a typed result back.

The package is not a new orchestrator or model router. It is the reusable
runtime layer that makes vendor agent SDKs feel consistent without hiding the
capabilities and constraints that make each SDK different.

## Current State (v0.1.1 — shipped 2026-06-11)

- Live on PyPI with optional extras `claude`, `codex`, `antigravity`, `all`;
  core import is dependency-free.
- Three runnable adapters with availability diagnostics, normalized events
  (redaction + truncation), incremental streaming (Claude, Antigravity), and
  tool-call audits (all three).
- Unsupported task inputs raise typed `UnsupportedTaskInputError` instead of
  being silently dropped; tolerated vendor-option drift is surfaced in
  `AgentResult.metadata["dropped_options"]`.
- 76 tests (73 default + 3 opt-in live smoke), strict mypy, ruff; CI matrix
  Python 3.10–3.13 × {core, all-extras}; PyPI publishing via trusted
  publishing, gated on the test job.
- ≈4.4k lines across `src/`, `tests/`, `docs/`, `examples/`.

## Core Value

Developers can run the same agentic task through Claude, Codex, or Antigravity
using one small, typed Python API while preserving the vendor-specific
capabilities needed for real work.

## Requirements

### Validated

- ✓ Publish the package as `agent-runtime-kit` on PyPI — v0.1.0/v0.1.1
- ✓ Support Python 3.10+ — v0.1.1 (CI-verified 3.10–3.13)
- ✓ Clean public API with no Mestre internals exposed — v0.1.1
- ✓ Shared runtime contract (task, capabilities, events, cancellation hooks,
  sessions, tool audits, structured output, artifacts, usage/cost, typed
  results) — v0.1.1
- ✓ Runnable adapters for Claude, Codex, and Antigravity — v0.1.1
- ✓ Optional vendor extras (`claude`, `codex`, `antigravity`, `all`) — v0.1.1
- ✓ Vendor capability differences surfaced explicitly (capability flags +
  typed rejections instead of silent drops) — v0.1.1
- ✓ Same-task example across all three runtimes — v0.1.1
- ✓ Credential-free unit tests + fake SDK surfaces + smoke-test paths —
  v0.1.1 (plus real-SDK contract tests)
- ✓ Documented auth, permissions, working directory, structured output, MCP,
  sessions, and vendor limitations — v0.1.1

### Active

- [ ] Mestre adopts `agent-runtime-kit` at its vendor-lane boundary
  (compatibility proven by tests; actual migration not yet started).
- [ ] Validate adapter behavior against live providers on a regular cadence
  (live smoke tests exist but have not been run with real credentials).

### Out of Scope

- Full Mestre orchestration, routing, fallback, benchmarking, optimization,
  and self-improvement loops — the package should be useful without becoming
  Mestre.
- Generic chat/completions abstraction — this package targets agentic SDKs
  that own tool loops and local/runtime context.
- Scraping or reusing unsupported local account credentials.
- A hosted service, UI, queue, control plane, or remote execution platform.
- Non-Python SDKs (revisit after adoption).
- Hiding vendor differences behind lowest-common-denominator behavior.

## Context

Shipped v0.1.1 on 2026-06-11, one day after the project started from Mestre's
vendor-lane implementation (`~/Github/mestre/mestre/vendor_lane/*`).

Tech: Python 3.10+, hatchling (PEP 639 license), uv-managed dev environment,
pytest/pytest-asyncio, ruff, strict mypy. Vendor pins of note: the `codex`
extra requires `openai-codex>=0.1.0b3` because earlier betas pin a CLI binary
with no glibc-Linux wheels; `uv` `required-environments` guards macOS arm64 +
Linux x86_64 resolution.

Known landscape risks: all three vendor SDKs move fast (codex is still beta;
antigravity pins exact pre-release binaries). `tests/test_sdk_contract.py`
introspects the installed SDK surfaces and is the primary drift tripwire.
Antigravity session data lives under `$XDG_CACHE_HOME/agent-runtime-kit`
(override via `data_dir=`).

A full-codebase review (2026-06-11, CODE-REVIEW.md, untracked) drove the
v0.1.1 hardening; its residual nits (cache parent dirs 0755 around the 0700
leaf; `token_count` keys now redacted in events) were accepted as-is.

## Constraints

- **Language**: Python package first - Mestre and all three target vendor SDK
  integrations are Python-facing for this work.
- **Python version**: Python 3.10+ - broad community compatibility matters more
  than matching Mestre's current Python 3.14-only project constraint.
- **Package name**: `agent-runtime-kit` (published; name secured on PyPI).
- **Vendor support**: Claude, Codex, and Antigravity must all be runnable;
  partial provider stubs are not enough.
- **Dependency model**: Vendor SDKs are optional extras.
- **Architecture**: Extract the runtime/adapters layer from Mestre, not the
  full orchestration and routing system.
- **API design**: Clean public API with a low-friction Mestre adoption path.
- **Authentication**: Stay within supported vendor SDK authentication
  mechanisms; no local credential scraping.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Publish as `agent-runtime-kit` | Name available and descriptive. | ✓ Good — published v0.1.0/v0.1.1 |
| Target Python 3.10+ | Community adoption over Mestre's 3.14 baseline. | ✓ Good — CI green 3.10–3.13 |
| Make all three runtimes runnable in v1 | Multi-vendor proof beats one adapter + stubs. | ✓ Good — three adapters shipped |
| Clean public API + Mestre migration support | Stand-alone package, practical adoption path. | ✓ Good — compat tests pass; adoption pending |
| Extract runtime/adapters, not orchestration | Keeps the package focused and reusable. | ✓ Good |
| Dependency-free core; vendor SDKs behind lazy optional extras (P1/P2) | Users install only what they need; core import never breaks. | ✓ Good |
| Normalized dict events with redaction/truncation (P2) | Observability without leaking secrets or flooding sinks. | ✓ Good |
| Fake-injected adapter tests keep CI credential-free (P3) | Fast default suite, no vendor accounts needed. | ⚠️ Revisit — fakes drifted from real SDKs; now paired with real-SDK contract tests |
| Reject unsupported inputs with typed errors instead of silent drops (v0.1.1) | The package's stated contract; silent drops misled callers (budget, tool filters, network). | ✓ Good |
| Conservative default permission posture per adapter (v0.1.1) | "Default" must not mean allow-everything on any runtime (was all-tools+allow-all on Antigravity). | ✓ Good |
| `codex` extra floor at `openai-codex>=0.1.0b3` + uv required-environments (v0.1.1) | Earlier betas pin a CLI binary with no glibc-Linux wheels; CI must install on ubuntu. | ✓ Good |
| Publish via trusted publishing, gated on tests (v0.1.1) | No long-lived tokens; broken builds can't ship. | ✓ Good |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `$gsd-transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `$gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check - still the right priority?
3. Audit Out of Scope - reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-06-11 after v0.1.1 milestone*
