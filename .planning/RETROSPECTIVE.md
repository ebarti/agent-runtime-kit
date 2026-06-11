# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v0.1.1 — First Public Release

**Shipped:** 2026-06-11
**Phases:** 5 | **Plans:** 15 (5 consolidated plan documents) | **Sessions:** 2

### What Was Built

- One typed async runtime API (`AgentTask` → `AgentResult` over an
  `AgentRuntime` protocol) spanning Claude Agent SDK, OpenAI Codex SDK, and
  Google Antigravity SDK, with a dependency-free core and per-vendor extras.
- Normalized event vocabulary with redaction/truncation, incremental streaming
  (Claude, Antigravity), and tool-call audits (all three adapters).
- Test pyramid that needs no credentials by default: injected-fake adapter
  tests, real-SDK surface contract tests, opt-in live smoke tests; CI across
  Python 3.10–3.13 in core-only and all-extras lanes; publish gated on tests.
- Two PyPI releases via trusted publishing: v0.1.0, then v0.1.1 hardening.

### What Worked

- Extracting a proven design (Mestre's vendor lane) instead of designing from
  scratch: five phases went plan → merged PR in one day.
- Injected-SDK fakes kept every default test credential-free and fast (<1s
  suite), which made the CI matrix cheap.
- A post-phase full-codebase review with real-SDK introspection (not just
  fakes) caught three release-blocking bugs the phase verifications missed.
- Subagent pipeline for the hardening pass (implement → package/docs →
  independent adversarial review) kept each context focused; the reviewer
  verified findings against installed SDKs rather than trusting reports.

### What Was Inefficient

- Fake-based contract tests drifted from the real SDKs (fabricated
  `BuiltinTools` helpers were coincidentally right; `McpStdioServer` fake was
  missing the required `name` field), so a guaranteed crash shipped in v0.1.0.
- Tests asserted adapter *responses* but not constructed *requests*; the
  inverted codex approval mapping survived because nothing asserted the
  mapping.
- Phase 4 was named "cross-runtime proof" but live proof was never run; the
  verification only covered injected fakes.
- Tooling friction: a renamed project directory left a broken venv, and a
  globally exported `UV_EXCLUDE_NEWER` made local `uv` resolution fail after
  re-locking; both cost debugging time.

### Patterns Established

- Optional vendor extras + lazy imports + `availability()` diagnostics as the
  standard adapter shape.
- "Reject, don't silently drop": unsupported task inputs raise
  `UnsupportedTaskInputError`; tolerated drops (claude option drift) must be
  observable (`metadata["dropped_options"]`).
- Real-SDK surface contract tests (`tests/test_sdk_contract.py`) that auto-skip
  without extras — the drift tripwire the fakes can't provide.
- Conventional-commit, stacked-PR delivery with CI lanes mirroring the two
  install profiles (core-only, all-extras).

### Key Lessons

1. Fakes validate translation logic, not vendor contracts — pair every fake
   with an introspection test against the installed SDK.
2. Assert what adapters *send* (options, kwargs, enum choices), not just what
   they return; permission/safety mappings deserve explicit per-mode tests.
3. Beta vendor SDKs pin platform-specific binaries — lock and CI must cover
   every target platform (the codex CLI binary had no glibc-Linux wheels until
   openai-codex 0.1.0b3).
4. Run the strict-typecheck gate in both dependency lanes; with extras
   installed, previously-needed `type: ignore` comments become errors.
5. Default permission postures must be compared across adapters as a set —
   "default" meant prompt-gated on Claude but all-tools-allow-all on
   Antigravity until the review caught it.

### Cost Observations

- Model mix: phases built under the GSD "quality" profile; the hardening pass
  ran as three Fable 5 subagents (~417k subagent tokens) plus the orchestrator.
- Sessions: 2 (2026-06-10 build, 2026-06-11 review/harden/release).
- Notable: the adversarial review subagent re-verified every claimed fix
  against the installed SDKs and still found zero false "fixed" claims —
  worth the token cost for a release gate.

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Sessions | Phases | Key Change |
|-----------|----------|--------|------------|
| v0.1.1 | 2 | 5 | Added real-SDK contract tests + CI lanes + publish gating after review found fake-drift bugs |

### Cumulative Quality

| Milestone | Tests | Coverage | Zero-Dep Additions |
|-----------|-------|----------|-------------------|
| v0.1.1 | 76 (73 + 3 opt-in live) | not measured | core remains dependency-free |

### Top Lessons (Verified Across Milestones)

1. (Single milestone so far — candidates to verify next: fakes need real-SDK
   contract twins; request-side assertions catch what response-side tests
   miss.)
