# Feature Research

**Domain:** Python multi-vendor agent runtime SDK
**Researched:** 2026-06-10
**Confidence:** HIGH

## Feature Landscape

### Table Stakes (Users Expect These)

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Installable PyPI package | Community users need a normal `pip install` path. | MEDIUM | Reserve/publish `agent-runtime-kit`; use optional extras. |
| Async runtime API | Vendor SDKs expose async sessions, streams, or calls. | MEDIUM | Public API should be async-first. |
| Runtime registry | Users need to discover and select available runtimes. | LOW | Mestre's registry is a good starting point. |
| Capability declarations | SDKs differ on MCP, working directory, session resume, structured output, and tool controls. | MEDIUM | Avoid pretending all providers support all inputs. |
| Typed task/result models | The core value is a consistent invocation and result shape. | MEDIUM | Include output, finish reason, session id, tool audits, usage/cost metadata, artifacts, and parsed output. |
| Claude adapter | One of the three target runtimes. | HIGH | Must support options, permissions, tools, MCP, cwd, resume, structured output where available. |
| Codex adapter | One of the three target runtimes. | HIGH | Must handle app-server thread lifecycle, model support checks, approval/sandbox settings, and structured output. |
| Antigravity adapter | One of the three target runtimes. | HIGH | Must handle API key requirement, capabilities/policies, MCP, workspace handling, structured output, and tool streams. |
| Events/streaming surface | Agent SDKs produce progress, tool calls, and output deltas. | HIGH | Use a vendor-neutral event vocabulary. |
| Missing-dependency behavior | Optional extras mean imports fail unless handled cleanly. | LOW | Adapter availability checks must be user-readable. |
| Documentation and examples | A community package needs quick proof. | MEDIUM | Include one same-task example across all three runtimes. |
| Tests with fake SDK surfaces | Live SDK tests are expensive/flaky. | MEDIUM | Fake modules should exercise option detection and result translation. |

### Differentiators (Competitive Advantage)

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Explicit vendor-difference matrix | Builds trust by showing what each SDK can and cannot do. | LOW | Generate docs from `AgentCapabilities`. |
| Compatibility adapter for Mestre | Provides immediate real-world consumer and migration path. | MEDIUM | Keep separate from core package API. |
| Structured output normalization | Useful for agent automation and eval workflows. | HIGH | Validate schema outputs where vendor supports them; fail clearly where not. |
| Tool-call audit normalization | Lets users inspect and log agent behavior across vendors. | HIGH | Must avoid leaking full sensitive payloads by default. |
| Permission abstraction | Makes local file/command execution safer and comparable. | HIGH | Must map to vendor-specific controls carefully. |
| OTel-friendly event sink | Helps production users integrate traces without choosing a tracing backend. | MEDIUM | Emit generic event mappings; do not require OTel SDK in core unless needed. |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| One universal "agent" object that hides all differences | Looks simple in demos. | Real provider differences matter for safety, tools, auth, and sessions. | Common task/result model plus runtime capabilities. |
| Full model routing and fallback | Useful in Mestre. | Turns the package into an orchestration framework. | Provide hooks; let callers choose runtimes. |
| Direct chat/completions support | Broadens market. | Blurs the agent-runtime purpose and competes with existing LLM SDKs. | Stay focused on agent SDKs that own tool loops. |
| Account-token reuse across vendors | Lower setup friction. | Often unsupported and security-sensitive. | Supported SDK auth docs and clear errors. |
| Live tests as primary validation | Seems realistic. | Requires credentials, costs money, and flakes. | Fake SDK tests plus optional live smoke tests. |

## Feature Dependencies

```text
Public task/result models
    -> Runtime registry
    -> Adapter availability checks
    -> Provider adapters
    -> Same-task examples

Capability declarations
    -> Feature validation
    -> Docs matrix
    -> Clear unsupported-input errors

Event vocabulary
    -> Adapter event translation
    -> Tool-call audit records
    -> Observability examples

Optional extras
    -> Missing dependency behavior
    -> Packaging docs
```

### Dependency Notes

- **Provider adapters require public models:** Avoid extracting vendor code before the package has a stable API target.
- **Docs matrix requires capability declarations:** The matrix should be generated or tested against runtime metadata so it does not drift.
- **Mestre migration requires compatibility boundaries:** Do not make the public package import Mestre.
- **Live smoke tests require optional configuration:** They should be opt-in and skipped without credentials.

## MVP Definition

### Launch With (v1)

- [ ] PyPI package `agent-runtime-kit` with Python 3.10+ metadata.
- [ ] Optional extras for `claude`, `codex`, `antigravity`, and `all`.
- [ ] Public async API for `AgentTask`, `AgentResult`, `AgentRuntime`, capability declarations, event sink, and registry.
- [ ] Runnable Claude, Codex, and Antigravity adapters.
- [ ] Same-task examples for all three runtimes.
- [ ] Fake SDK tests for missing dependency, unsupported features, success, failure, structured output, session id, and tool audit translation.
- [ ] Docs for auth, permissions, cwd/workspace behavior, MCP, structured output, sessions, and known limitations.

### Add After Validation (v1.x)

- [ ] Mestre compatibility package/module.
- [ ] Optional OTel integration helpers.
- [ ] Additional live smoke-test harness.
- [ ] More fine-grained stream event types.
- [ ] Provider version compatibility matrix.

### Future Consideration (v2+)

- [ ] TypeScript package if community demand appears.
- [ ] Direct SDK/chat model adapters if the project scope expands deliberately.
- [ ] Orchestration helpers, routing, and fallback policies.
- [ ] Managed-agent or hosted sandbox integrations beyond local SDKs.

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| PyPI packaging and extras | HIGH | MEDIUM | P1 |
| Public runtime contract | HIGH | MEDIUM | P1 |
| Capability declarations | HIGH | LOW | P1 |
| Claude adapter | HIGH | HIGH | P1 |
| Codex adapter | HIGH | HIGH | P1 |
| Antigravity adapter | HIGH | HIGH | P1 |
| Same-task examples | HIGH | MEDIUM | P1 |
| Fake SDK tests | HIGH | MEDIUM | P1 |
| Mestre compatibility adapter | MEDIUM | MEDIUM | P2 |
| OTel helper integration | MEDIUM | MEDIUM | P2 |
| Full routing/fallback | LOW for core package | HIGH | P3 |

**Priority key:**
- P1: Must have for launch
- P2: Should have, add when possible
- P3: Nice to have, future consideration

## Competitor Feature Analysis

| Feature | Vendor SDKs | General agent frameworks | Our Approach |
|---------|-------------|--------------------------|--------------|
| Agent execution | Each vendor has its own API and auth model. | Usually framework-owned tool loop. | Normalize vendor-owned agent SDK execution. |
| Tool loop | Built into Claude/Codex/Antigravity. | Often implemented in framework. | Do not reimplement vendor tool loops; translate inputs and results. |
| Multi-provider support | Vendor-specific. | Often model/chat oriented rather than coding-agent SDK oriented. | Focus specifically on coding-agent runtime adapters. |
| Observability | Vendor-specific or absent. | Framework-specific. | Provide neutral events and optional tracing hooks. |

## Sources

- Claude Agent SDK docs: https://docs.anthropic.com/en/docs/claude-code/sdk
- Claude Python SDK reference: https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-python
- Codex SDK docs: https://developers.openai.com/codex/sdk
- Google Antigravity SDK README: https://github.com/google-antigravity/antigravity-sdk-python
- Mestre runtime contract: `~/Github/mestre/mestre/vendor_lane/agent_protocol.py`
- Mestre runtime registry: `~/Github/mestre/mestre/execution/agent/registry.py`
- Mestre vendor adapters: `~/Github/mestre/mestre/vendor_lane/backends/`

---
*Feature research for: Python multi-vendor agent runtime SDK*
*Researched: 2026-06-10*
