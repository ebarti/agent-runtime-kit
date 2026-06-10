# Roadmap: AgentSDK

## Overview

The v1.0 milestone turns the empty `Agent-SDK` project into a publishable
Python package named `agent-runtime-kit`. The build path starts with a small
installable core and public runtime contract, adds the fake-SDK harness needed
to keep vendor drift under control, then delivers Claude/Codex adapters,
Antigravity plus cross-runtime proof, and finally release-ready docs,
packaging, and smoke-test guidance.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [x] **Phase 1: Core Runtime Skeleton** - Installable core package with public task/result/runtime contracts.
- [x] **Phase 2: Events and Test Harness** - Optional extras skeleton, event system, and fake SDK contract tests.
- [x] **Phase 3: Claude and Codex Runtimes** - First two real vendor adapters through the shared API.
- [ ] **Phase 4: Antigravity and Cross-Runtime Proof** - Third adapter plus same-task multi-runtime proof and Mestre compatibility checks.
- [ ] **Phase 5: Public Release Readiness** - Documentation, capability matrix, live-smoke guidance, and PyPI publish checklist.

## Phase Details

### Phase 1: Core Runtime Skeleton
**Goal**: A developer can install/import the core package locally and run a fake runtime through the public async API without any vendor SDK installed.
**Mode:** mvp
**Depends on**: Nothing (first phase)
**Requirements**: [PKG-03, PKG-04, CORE-01, CORE-02, CORE-03, CORE-04, CORE-05, CORE-06, CORE-07, TEST-03, TEST-04]
**Success Criteria** (what must be TRUE):
  1. Developer can import `agent_runtime_kit` in an environment with no vendor SDKs installed.
  2. Developer can create an `AgentTask`, execute a fake runtime, and receive an `AgentResult`.
  3. Runtime capability checks and unsupported-feature errors are typed and covered by tests.
  4. Ruff and static-analysis commands validate the initial public API.
**Plans**: 3 plans

Plans:
- [ ] 01-01: Package scaffolding and Python 3.10+ metadata
- [ ] 01-02: Public task/result/runtime/capability/error models
- [ ] 01-03: Registry, availability diagnostics, lint, and type/static checks

### Phase 2: Events and Test Harness
**Goal**: A developer can observe normalized fake-runtime events and the package has the fake SDK harness required to test real adapters without live credentials.
**Mode:** mvp
**Depends on**: Phase 1
**Requirements**: [PKG-02, EVNT-01, EVNT-02, EVNT-03, EVNT-04, EVNT-05, TEST-01, TEST-02]
**Success Criteria** (what must be TRUE):
  1. Core tests pass without Claude, Codex, or Antigravity SDKs installed.
  2. Developer can attach an event sink and receive started, completed, failed, output, tool, and vendor-turn events from a fake runtime.
  3. Event payload defaults summarize or truncate high-volume fields.
  4. Fake SDK fixtures can simulate success, failure, missing dependency, unsupported input, timeout, session id, structured output, and tool events.
**Plans**: 3 plans

Plans:
- [ ] 02-01: Optional extras skeleton and dependency isolation tests
- [ ] 02-02: Event vocabulary, event sink, and redaction/truncation defaults
- [ ] 02-03: Fake SDK harness and adapter contract test utilities

### Phase 3: Claude and Codex Runtimes
**Goal**: A developer can run real Claude and Codex agent tasks through the shared runtime API with clear diagnostics and provider-specific capability handling.
**Mode:** mvp
**Depends on**: Phase 2
**Requirements**: [ADPT-01, ADPT-02, ADPT-03, ADPT-04, DOCS-02]
**Success Criteria** (what must be TRUE):
  1. Developer can install the Claude extra and run a Claude Agent SDK task through `agent-runtime-kit`.
  2. Developer can install the Codex extra and run a Codex SDK task through `agent-runtime-kit`.
  3. Claude and Codex adapters fail clearly for missing SDKs, missing setup, unsupported fields, and unsupported models.
  4. The quickstart demonstrates one runtime end to end through the public API.
**Plans**: 3 plans

Plans:
- [ ] 03-01: Claude Agent SDK adapter and tests
- [ ] 03-02: Codex SDK adapter and tests
- [ ] 03-03: One-runtime quickstart and provider diagnostics docs

### Phase 4: Antigravity and Cross-Runtime Proof
**Goal**: A developer can run the same task through Claude, Codex, and Antigravity, and the public API can represent Mestre's current runtime needs.
**Mode:** mvp
**Depends on**: Phase 3
**Requirements**: [ADPT-05, ADPT-06, ADPT-07, TEST-06, DOCS-03]
**Success Criteria** (what must be TRUE):
  1. Developer can install the Antigravity extra and run an Antigravity SDK task through `agent-runtime-kit`.
  2. Antigravity adapter maps auth, workspace, permissions, MCP, structured output, sessions, and tool/event behavior where supported.
  3. Same-task example runs through all three runtime kinds with one public API shape.
  4. Compatibility tests prove the public API can represent the fields Mestre currently uses from its vendor-lane contract.
**Plans**: 3 plans

Plans:
- [ ] 04-01: Google Antigravity SDK adapter and tests
- [ ] 04-02: Same-task three-runtime example
- [ ] 04-03: Mestre compatibility field audit and tests

### Phase 5: Public Release Readiness
**Goal**: The package is ready for a first public PyPI release with documentation, capability matrix, optional live smoke tests, and a final publish checklist.
**Mode:** mvp
**Depends on**: Phase 4
**Requirements**: [PKG-01, PKG-05, TEST-05, DOCS-01, DOCS-04, DOCS-05, DOCS-06]
**Success Criteria** (what must be TRUE):
  1. README and provider docs explain what the package is, what it is not, and how to configure each runtime.
  2. Capability matrix documents MCP, working directory, sessions, structured output, permissions, streaming, and tool-audit behavior for each runtime.
  3. Optional live smoke tests are documented and skipped unless explicit credentials/runtime flags are present.
  4. PyPI publish checklist includes a fresh `agent-runtime-kit` name check and the package can be built for release.
**Plans**: 3 plans

Plans:
- [ ] 05-01: README, provider setup docs, and capability matrix
- [ ] 05-02: Optional live smoke test harness and documentation
- [ ] 05-03: Build, publish checklist, and Mestre migration notes

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Core Runtime Skeleton | 3/3 | Complete | 2026-06-10 |
| 2. Events and Test Harness | 3/3 | Complete | 2026-06-10 |
| 3. Claude and Codex Runtimes | 3/3 | Complete | 2026-06-10 |
| 4. Antigravity and Cross-Runtime Proof | 0/3 | Not started | - |
| 5. Public Release Readiness | 0/3 | Not started | - |
