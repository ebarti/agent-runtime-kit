# Requirements: AgentSDK

**Defined:** 2026-06-10
**Core Value:** Developers can run the same agentic task through Claude, Codex,
or Antigravity using one small, typed Python API while preserving the
vendor-specific capabilities needed for real work.

## v1 Requirements

Requirements for the first public release of `agent-runtime-kit`. Each maps to
roadmap phases.

### Packaging

- [ ] **PKG-01**: Developer can install the core package from PyPI as
  `agent-runtime-kit` without installing any vendor SDK.
- [ ] **PKG-02**: Developer can install vendor-specific extras for `claude`,
  `codex`, `antigravity`, and `all`.
- [ ] **PKG-03**: Package metadata declares Python 3.10+ support.
- [ ] **PKG-04**: Core package import succeeds when no vendor extras are
  installed.
- [ ] **PKG-05**: Package publishing checklist includes a fresh PyPI name
  availability check for `agent-runtime-kit`.

### Core Runtime API

- [ ] **CORE-01**: Developer can create a typed `AgentTask` with goal, optional
  system prompt, working directory, permissions, session/resume data, metadata,
  MCP server config, and output schema.
- [ ] **CORE-02**: Developer receives a typed `AgentResult` with output,
  finish reason, error, session id, artifacts, tool-call audits, usage/cost
  metadata, and parsed structured output when available.
- [ ] **CORE-03**: Developer can implement or use an async `AgentRuntime`
  protocol with `run(task)` and `cancel(task_id)`.
- [ ] **CORE-04**: Developer can inspect each runtime's declared capabilities
  before dispatching a task.
- [ ] **CORE-05**: Runtime rejects unsupported task inputs with clear typed
  errors instead of silently dropping fields.
- [ ] **CORE-06**: Developer can register and resolve runtimes through a small
  runtime registry.
- [ ] **CORE-07**: Runtime availability checks distinguish missing package,
  missing credentials, unsupported model/runtime, and other setup failures.

### Events and Observability

- [ ] **EVNT-01**: Developer can attach an async event sink to receive
  `agent.task.started` events.
- [ ] **EVNT-02**: Developer can receive `agent.task.completed` and
  `agent.task.failed` events with normalized task/result metadata.
- [ ] **EVNT-03**: Developer can receive normalized output-delta events for
  streamed text where a vendor exposes streaming.
- [ ] **EVNT-04**: Developer can receive normalized tool-requested and
  tool-completed audit events where a vendor exposes tool activity.
- [ ] **EVNT-05**: Event payloads truncate or summarize sensitive/high-volume
  data by default.

### Provider Adapters

- [ ] **ADPT-01**: Developer can run a task through Claude Agent SDK using the
  shared runtime API.
- [ ] **ADPT-02**: Claude adapter supports working directory, permission mode,
  MCP config, allowed/disallowed tools, session resume, structured output, and
  clear missing-SDK/auth diagnostics where supported by the installed SDK.
- [ ] **ADPT-03**: Developer can run a task through OpenAI Codex SDK using the
  shared runtime API.
- [ ] **ADPT-04**: Codex adapter supports local app-server/thread lifecycle,
  working directory, approval/sandbox mapping, session resume, structured
  output, and model availability diagnostics.
- [ ] **ADPT-05**: Developer can run a task through Google Antigravity SDK using
  the shared runtime API.
- [ ] **ADPT-06**: Antigravity adapter supports API-key diagnostics,
  working-directory/workspace mapping, permission/capability mapping, MCP
  config, structured output, session id, and tool/event translation where
  supported by the installed SDK.
- [ ] **ADPT-07**: All adapters preserve vendor-specific metadata needed for
  debugging without making that metadata the primary public API.

### Testing and Quality

- [ ] **TEST-01**: Core tests pass without Claude, Codex, or Antigravity SDKs
  installed.
- [ ] **TEST-02**: Fake SDK tests cover successful invocation, vendor errors,
  missing dependency, unsupported task input, timeout, session id, structured
  output, and event translation for each adapter.
- [ ] **TEST-03**: Type-check or static-analysis workflow validates the public
  API surface.
- [ ] **TEST-04**: Ruff lint/format workflow passes for the package.
- [ ] **TEST-05**: Optional live smoke tests are documented and skipped unless
  explicit credentials/runtime flags are present.
- [ ] **TEST-06**: Compatibility tests verify the public API can represent the
  fields Mestre currently needs from its vendor-lane runtime contract.

### Documentation and Examples

- [ ] **DOCS-01**: README explains what the package is, what it is not, and how
  it differs from vendor SDKs and full agent frameworks.
- [ ] **DOCS-02**: Quickstart shows installing the package and running one task
  through one runtime.
- [ ] **DOCS-03**: Example shows the same task running through Claude, Codex,
  and Antigravity with the shared API.
- [ ] **DOCS-04**: Provider setup docs cover auth, required extras, local
  runtime requirements, and known limitations for each vendor.
- [ ] **DOCS-05**: Capability matrix documents MCP, working directory, session
  resume, structured output, permissions, streaming, and tool-audit support for
  each runtime.
- [ ] **DOCS-06**: Migration notes describe how Mestre can adopt
  `agent-runtime-kit` without moving its routing, fallback, benchmarking, or
  self-improvement layers into the package.

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Integrations

- **INTG-01**: Package provides optional OpenTelemetry helper functions for
  converting events into spans or span events.
- **INTG-02**: Package provides a first-class Mestre compatibility module if
  the initial migration notes are not enough.
- **INTG-03**: Package maintains a generated provider compatibility/version
  matrix.

### Additional Runtime Scope

- **RUNT-01**: Package evaluates whether direct chat/completions adapters
  belong in a separate package or future major version.
- **RUNT-02**: Package evaluates support for additional agent SDKs after the
  first three providers are stable.
- **RUNT-03**: Package evaluates a synchronous convenience wrapper over the
  async core.

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Full model routing and fallback | Belongs in applications such as Mestre; too broad for a focused runtime SDK. |
| Benchmarking and self-optimization loops | Product-specific and not required for a useful public package. |
| Hosted service, queue, UI, or control plane | The first release is a local Python library. |
| Unsupported local credential scraping | Security-sensitive and outside supported vendor auth paths. |
| Non-Python SDKs | Python package first; revisit after v1 adoption. |
| Lowest-common-denominator provider abstraction | The package must preserve important vendor capability differences. |
| Mandatory live provider tests in default CI | Would require credentials, cost money, and create flaky contributor workflows. |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| PKG-01 | Phase 5 | Ready (publish pending) |
| PKG-02 | Phase 2 | Complete |
| PKG-03 | Phase 1 | Complete |
| PKG-04 | Phase 1 | Complete |
| PKG-05 | Phase 5 | Complete |
| CORE-01 | Phase 1 | Complete |
| CORE-02 | Phase 1 | Complete |
| CORE-03 | Phase 1 | Complete |
| CORE-04 | Phase 1 | Complete |
| CORE-05 | Phase 1 | Complete |
| CORE-06 | Phase 1 | Complete |
| CORE-07 | Phase 1 | Complete |
| EVNT-01 | Phase 2 | Complete |
| EVNT-02 | Phase 2 | Complete |
| EVNT-03 | Phase 2 | Complete |
| EVNT-04 | Phase 2 | Complete |
| EVNT-05 | Phase 2 | Complete |
| ADPT-01 | Phase 3 | Complete |
| ADPT-02 | Phase 3 | Complete |
| ADPT-03 | Phase 3 | Complete |
| ADPT-04 | Phase 3 | Complete |
| ADPT-05 | Phase 4 | Complete |
| ADPT-06 | Phase 4 | Complete |
| ADPT-07 | Phase 4 | Complete |
| TEST-01 | Phase 2 | Complete |
| TEST-02 | Phase 2 | Complete |
| TEST-03 | Phase 1 | Complete |
| TEST-04 | Phase 1 | Complete |
| TEST-05 | Phase 5 | Complete |
| TEST-06 | Phase 4 | Complete |
| DOCS-01 | Phase 5 | Complete |
| DOCS-02 | Phase 3 | Complete |
| DOCS-03 | Phase 4 | Complete |
| DOCS-04 | Phase 5 | Complete |
| DOCS-05 | Phase 5 | Complete |
| DOCS-06 | Phase 5 | Complete |

**Coverage:**
- v1 requirements: 36 total
- Mapped to phases: 36
- Unmapped: 0

---
*Requirements defined: 2026-06-10*
*Last updated: 2026-06-10 after roadmap traceability mapping*
