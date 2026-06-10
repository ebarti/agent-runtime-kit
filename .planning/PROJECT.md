# agent-runtime-kit

## What This Is

`agent-runtime-kit` is a Python package that gives developers one clean API for running agentic
coding tasks through Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK. It extracts the useful vendor-runtime ideas from Mestre while
remaining independently useful to the community: install it, choose a runtime,
run a task, stream/inspect events, and get a typed result back.

The package is not a new orchestrator or model router. It is the reusable
runtime layer that makes vendor agent SDKs feel consistent without hiding the
capabilities and constraints that make each SDK different.

## Core Value

Developers can run the same agentic task through Claude, Codex, or Antigravity
using one small, typed Python API while preserving the vendor-specific
capabilities needed for real work.

## Requirements

### Validated

(None yet - ship to validate)

### Active

- [ ] Publish the package as `agent-runtime-kit` on PyPI.
- [ ] Support Python 3.10+ so the package is broadly usable and aligns with
      current vendor SDK lower bounds.
- [ ] Provide a clean public API that does not expose Mestre internals.
- [ ] Keep a low-friction Mestre adoption path through compatibility adapters
      or migration helpers.
- [ ] Provide a shared runtime contract for agentic work: task input, runtime
      capability metadata, event emission, cancellation, session/resume handles,
      tool-call audit records, structured output, artifacts, cost/usage
      metadata, and typed results.
- [ ] Implement runnable adapters for Claude Agent SDK, OpenAI Codex SDK, and
      Google Antigravity SDK in the first public release.
- [ ] Make vendor dependencies optional, with extras such as `claude`, `codex`,
      `antigravity`, and `all`.
- [ ] Surface vendor capability differences explicitly instead of pretending
      all SDKs support the same features.
- [ ] Include examples that run the same task through all three runtimes.
- [ ] Include unit tests with fake SDK surfaces and at least smoke-test paths
      that prove adapter construction and invocation behavior.
- [ ] Document authentication, permissions, working-directory behavior,
      structured output behavior, MCP support, session behavior, and known
      vendor limitations.

### Out of Scope

- Full Mestre orchestration, routing, fallback, benchmarking, optimization, and
  self-improvement loops - the package should be useful without becoming
  Mestre.
- Generic chat/completions abstraction - this package targets agentic SDKs that
  own tool loops and local/runtime context.
- Scraping or reusing unsupported local account credentials - use each vendor's
  supported authentication path.
- A hosted service, UI, queue, control plane, or remote execution platform.
- Non-Python SDKs for the initial release.
- Hiding vendor differences behind lowest-common-denominator behavior.

## Context

The project starts from Mestre's live vendor-lane implementation in
`~/Github/mestre`, especially:

- `mestre/vendor_lane/agent_protocol.py` - existing typed contract for
  `AgentTask`, `AgentResult`, capabilities, session resume state, MCP config,
  event sinks, and tool-call audits.
- `mestre/vendor_lane/events.py` - canonical task/tool/output/vendor-turn event
  vocabulary.
- `mestre/vendor_lane/backends/claude_sdk.py` - Claude Agent SDK adapter logic.
- `mestre/vendor_lane/backends/codex_sdk.py` - Codex SDK adapter logic.
- `mestre/vendor_lane/backends/antigravity_sdk.py` - Antigravity SDK adapter
  logic.
- `mestre/execution/agent/registry.py` - runtime registry pattern.
- `mestre/llm/policy.py` - boundary to avoid over-extracting full routing
  policy into this package.

Official vendor docs checked during initialization:

- Claude Agent SDK: https://docs.anthropic.com/en/docs/claude-code/sdk
- Codex SDK: https://developers.openai.com/codex/sdk
- Google Antigravity SDK:
  https://github.com/google-antigravity/antigravity-sdk-python

The current PyPI name check found `agent-runtime-kit` available on
2026-06-10. This availability must be rechecked immediately before publishing.

## Constraints

- **Language**: Python package first - Mestre and all three target vendor SDK
  integrations are Python-facing for this work.
- **Python version**: Python 3.10+ - broad community compatibility matters more
  than matching Mestre's current Python 3.14-only project constraint.
- **Package name**: Use `agent-runtime-kit` unless a later publishing check
  shows the name is no longer available.
- **Vendor support**: Claude, Codex, and Antigravity must all be runnable in
  v1; partial provider stubs are not enough for a useful community release.
- **Dependency model**: Vendor SDKs should be optional extras so users can
  install only the runtimes they need.
- **Architecture**: Extract the runtime/adapters layer from Mestre, not the full
  orchestration and routing system.
- **API design**: Prefer a clean public API, but keep compatibility adapters or
  migration helpers so Mestre can adopt the package without excessive churn.
- **Authentication**: Stay within supported vendor SDK authentication
  mechanisms; do not build brittle local credential scraping into the core.

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Publish as `agent-runtime-kit` | The name was available on PyPI during initialization and accurately describes a runtime/adapters library. | Pending |
| Target Python 3.10+ | Vendor SDK docs support Python 3.10+, and community adoption is more important than mirroring Mestre's Python 3.14 baseline. | Pending |
| Make all three runtimes runnable in v1 | Community usefulness depends on a real multi-vendor proof, not a single polished adapter plus placeholders. | Pending |
| Use a clean public API with Mestre migration support | The package should stand on its own while still making future Mestre adoption practical. | Pending |
| Extract runtime/adapters, not full orchestration | Mestre's routing, fallback, benchmarking, and self-improvement loops would make the package too broad for a first release. | Pending |

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
*Last updated: 2026-06-10 after initialization*
