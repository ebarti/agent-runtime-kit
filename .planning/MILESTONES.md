# Milestones

## v0.1.1 First Public Release (Shipped: 2026-06-11)

**Phases completed:** 5 phases · 15 plans (5 consolidated plan documents)
**Delivered:** One typed async runtime API over the Claude, Codex, and
Antigravity agent SDKs, published to PyPI with tests, docs, and CI.

**Key accomplishments:**

1. Dependency-free typed core: `AgentTask`/`AgentResult`/`AgentRuntime`
   protocol, capability advertisement, typed error taxonomy, and a runtime
   registry — importable with zero vendor SDKs installed.
2. Three runnable vendor adapters (Claude Agent SDK, OpenAI Codex SDK, Google
   Antigravity SDK) behind optional extras, with availability diagnostics that
   distinguish missing package, missing credentials, and setup failures.
3. Normalized event system (task started/completed/failed, output deltas, tool
   requested/completed, vendor turns) with secret redaction and truncation
   defaults; genuine incremental streaming for Claude and Antigravity.
4. Credential-free test strategy: injected-fake adapter tests, real-SDK surface
   contract tests (auto-skip without extras), and opt-in live smoke tests;
   CI matrix across Python 3.10–3.13 × {core, all-extras} dependency lanes.
5. Post-phase hardening shipped as v0.1.1: a full-codebase review found and
   fixed an inverted codex approval-mode mapping, a guaranteed antigravity MCP
   crash, world-shared `/tmp` session storage, and silently dropped task
   fields — now explicit typed rejections per the project's no-silent-drops
   contract.
6. Published to PyPI via trusted publishing, gated on the test matrix:
   releases v0.1.0 (initial) and v0.1.1 (hardening).

**Stats:** 72 files changed, ~8.8k lines added over the milestone (≈4.4k lines
in `src/`, `tests/`, `docs/`, `examples/`); built 2026-06-10 → 2026-06-11
(~14 hours wall clock); git range `debddac` → `a5dab0a`; tags `v0.1.0`,
`v0.1.1`.

Known deferred items at close: none (open-artifact audit clear).

---
