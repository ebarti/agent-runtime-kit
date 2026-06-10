# Project Research Summary

**Project:** AgentSDK / `agent-runtime-kit`
**Domain:** Python multi-vendor agent runtime SDK
**Researched:** 2026-06-10
**Confidence:** HIGH

## Executive Summary

This project should be a focused Python runtime/adapters package, not a full
agent orchestration framework. Mestre already contains the right seed shape:
an `AgentRuntime` protocol, typed task/result models, runtime capabilities,
event sink, tool-call audit records, session handles, and provider-specific
adapters for Claude, Codex, and Antigravity. The new package should turn that
idea into a clean public API that community users can install and understand
without learning Mestre.

The best first release is an installable PyPI package named
`agent-runtime-kit`, targeting Python 3.10+, with optional extras for
`claude`, `codex`, `antigravity`, and `all`. It should prove value by running
the same task through all three vendor SDKs while surfacing capability
differences explicitly. The core should avoid vendor imports at package import
time and should not absorb Mestre's routing, fallback, benchmark, or
self-improvement systems.

The main risk is vendor SDK drift. Claude Agent SDK is already newer on PyPI
than Mestre's current pin; Codex is still a beta package; Antigravity depends
on compiled PyPI wheels. The roadmap should isolate vendor-specific code,
write fake-SDK contract tests, document supported versions, and make live
provider tests optional.

## Key Findings

### Recommended Stack

Use Python 3.10+ with an async-first public API. Keep core dependencies small,
make vendor SDKs optional extras, and use tests/fakes to protect against
provider API drift.

**Core technologies:**
- Python 3.10+: package baseline and vendor SDK compatibility.
- `claude-agent-sdk`: Claude runtime adapter.
- `openai-codex` plus `openai-codex-cli-bin`: Codex runtime adapter.
- `google-antigravity`: Antigravity runtime adapter.
- Pydantic/dataclasses: typed public task and result models.
- pytest/ruff: fast public package quality loop.
- Optional OpenTelemetry helpers: event/trace integration without making OTel a hard runtime commitment unless needed.

### Expected Features

**Must have (table stakes):**
- PyPI package with optional extras.
- Async `AgentRuntime.run(AgentTask) -> AgentResult` style contract.
- Runtime registry and availability diagnostics.
- Runtime capabilities and unsupported-feature errors.
- Claude, Codex, and Antigravity adapters runnable in v1.
- Event sink with task/tool/output/vendor-turn events.
- Same-task examples across all three runtimes.
- Fake SDK tests that run without credentials.

**Should have (competitive):**
- Vendor capability matrix.
- Structured output normalization and validation.
- Tool-call audit normalization.
- Mestre compatibility/migration adapter.
- OTel-friendly event translation.

**Defer (v2+):**
- Full model routing and fallback.
- Benchmarking and self-optimization loops.
- Direct chat/completions abstraction.
- Hosted service, queue, UI, or control plane.
- Non-Python SDKs.

### Architecture Approach

The package should have a small core plus optional adapters. The core owns
types, runtime protocol, registry, events, errors, permissions, and structured
output helpers. Adapter modules own all vendor SDK imports and translation.
Compatibility with Mestre belongs in a separate namespace or downstream bridge.

**Major components:**
1. Core public API - typed task/result/capability/session/event models.
2. Runtime registry - maps runtime ids to factories and capabilities.
3. Adapter modules - Claude, Codex, and Antigravity SDK translators.
4. Validation layer - checks capabilities, missing deps, auth, cwd, session, MCP, and schema support.
5. Examples/docs - show same-task usage and provider-specific setup.
6. Compatibility layer - maps Mestre's current runtime types when Mestre adopts the package.

### Critical Pitfalls

1. **Hiding vendor differences** - prevent with capability declarations and unsupported-feature errors.
2. **Import-time optional dependency failures** - prevent with lazy imports and extras.
3. **SDK surface drift** - prevent with adapter isolation, fake SDK tests, and version matrix.
4. **Extracting too much Mestre** - keep routing, fallback, benchmarks, and optimization out of v1.
5. **Unsafe authentication shortcuts** - use only supported vendor auth paths.
6. **Required live provider tests** - make live tests opt-in; keep CI fake-based.

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: Package Foundation and Public Contract
**Rationale:** All provider work depends on a stable public API and package layout.
**Delivers:** `src/agent_runtime_kit`, core types, runtime protocol, registry, errors, permission model, event sink, packaging metadata, optional extras skeleton.
**Addresses:** PyPI installability, Python 3.10+, no vendor imports in core.
**Avoids:** Import-time optional dependency failures and Mestre-specific public API.

### Phase 2: Adapter Contract Tests and Fake SDK Harness
**Rationale:** Vendor SDKs drift; tests should define what the package expects before moving all adapters.
**Delivers:** fake SDK fixtures, adapter availability tests, unsupported-feature tests, event/result translation tests.
**Uses:** pytest, pytest-asyncio/anyio.
**Implements:** Default CI that does not require credentials.

### Phase 3: Claude Adapter
**Rationale:** Claude Agent SDK has a rich feature set and current docs for permissions, tools, MCP, sessions, and structured output.
**Delivers:** Claude runtime adapter, availability diagnostics, docs/example.
**Addresses:** SDK option-surface drift and supported auth paths.

### Phase 4: Codex Adapter
**Rationale:** Codex requires local app-server/thread handling and model availability checks, so it should be isolated after the contract is proven.
**Delivers:** Codex runtime adapter, app-server/model diagnostics, docs/example.
**Uses:** `openai-codex`, `openai-codex-cli-bin`.

### Phase 5: Antigravity Adapter
**Rationale:** Antigravity has distinct capability/policy/workspace behavior and should be implemented with the same contract after Claude/Codex patterns are established.
**Delivers:** Antigravity runtime adapter, capability/policy mapping, docs/example.
**Uses:** `google-antigravity`.

### Phase 6: Public Release Readiness
**Rationale:** A community package needs docs, examples, packaging, CI, and a final name check before release.
**Delivers:** README, quickstart, provider notes, capability matrix, same-task example, optional live smoke test docs, PyPI publish checklist.
**Avoids:** Shipping a library that works only for Mestre.

### Phase Ordering Rationale

- Public contract comes before adapter migration so vendor code has a clean target.
- Fake SDK tests come before full adapters because current vendor surfaces are moving.
- Provider phases are separated because each SDK has different auth, tools, sessions, and structured-output behavior.
- Release readiness comes after all three adapters are runnable so the first public release is genuinely useful.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 3:** Claude Agent SDK constructor/option fields and structured output behavior should be verified against the installed version.
- **Phase 4:** Codex Python SDK/app-server surface should be verified locally because docs and beta packages can diverge.
- **Phase 5:** Antigravity policy/capability APIs and compiled-wheel behavior should be verified against the installed wheel.
- **Phase 6:** PyPI name availability and package metadata should be rechecked immediately before publishing.

Phases with standard patterns:
- **Phase 1:** Python package scaffolding, core types, and registry patterns are well understood.
- **Phase 2:** Fake SDK tests and optional dependency import tests are standard Python package work.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Verified against official docs, PyPI metadata, and Mestre pins. |
| Features | HIGH | Directly follows user goals plus live Mestre runtime implementation. |
| Architecture | HIGH | Mestre already validates the runtime contract shape. |
| Pitfalls | HIGH | Based on source code boundaries, official docs, and known vendor SDK drift. |

**Overall confidence:** HIGH

### Gaps to Address

- **PyPI ownership:** `agent-runtime-kit` was available during initialization but must be rechecked before publish.
- **Installed SDK surfaces:** Plan phases should inspect current installed packages or install into the project env before adapter implementation.
- **Mestre migration:** The exact adapter from Mestre to package types should be designed after the public API exists.
- **Live smoke tests:** Need credentials and local runtime setup; should remain optional.

## Sources

### Primary (HIGH confidence)

- https://docs.anthropic.com/en/docs/claude-code/sdk - Claude Agent SDK overview.
- https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-python - Claude Agent SDK Python reference.
- https://developers.openai.com/codex/sdk - Codex SDK documentation.
- https://github.com/google-antigravity/antigravity-sdk-python - Google Antigravity SDK README and examples.
- `~/Github/mestre/mestre/vendor_lane/agent_protocol.py` - source contract to extract.
- `~/Github/mestre/mestre/vendor_lane/events.py` - event vocabulary to adapt.
- `~/Github/mestre/mestre/vendor_lane/backends/` - current provider adapter implementations.

### Secondary (MEDIUM confidence)

- PyPI package metadata for `claude-agent-sdk`, `openai-codex`, `openai-codex-cli-bin`, `google-antigravity`, `google-genai`, and OpenTelemetry packages, checked on 2026-06-10.
- `~/Github/mestre/docs/plans/implemented/RFC_LOCAL_AUTH_RUNTIME.md` - prior Mestre auth/runtime boundary rationale.

---
*Research completed: 2026-06-10*
*Ready for roadmap: yes*
