# Pitfalls Research

**Domain:** Python multi-vendor agent runtime SDK
**Researched:** 2026-06-10
**Confidence:** HIGH

## Critical Pitfalls

### Pitfall 1: Hiding Vendor Differences

**What goes wrong:**
The package exposes one API but silently ignores unsupported inputs such as
MCP servers, output schemas, working directories, permission modes, session
resume handles, or tool descriptors.

**Why it happens:**
Generic SDKs often optimize for a clean demo and treat all providers as the
same kind of model call.

**How to avoid:**
Make runtime capabilities part of the public API. Validate task inputs before
invoking a vendor SDK. Fail with typed unsupported-feature errors when an
adapter cannot honor a field.

**Warning signs:**
Tests assert only final text output and do not check unsupported input behavior.

**Phase to address:**
Phase 1, when the public contract is created.

---

### Pitfall 2: Import-Time Optional Dependency Failures

**What goes wrong:**
`import agent_runtime_kit` fails because a user did not install every vendor SDK.

**Why it happens:**
Adapter modules import vendor packages at top level.

**How to avoid:**
Keep vendor imports lazy and provide optional extras. Core imports should work
with no vendor SDK installed.

**Warning signs:**
Tests cannot import core package in a clean environment without `claude`,
`codex`, and `antigravity` extras.

**Phase to address:**
Phase 1, alongside package layout.

---

### Pitfall 3: SDK Surface Drift

**What goes wrong:**
A vendor SDK changes options, result objects, stream events, or authentication
behavior and breaks the adapter.

**Why it happens:**
These SDKs are active and some are beta or early-stage. Live PyPI metadata
already shows Claude Agent SDK newer than Mestre's current pin.

**How to avoid:**
Use fake SDK surface tests for every adapter. Detect constructor fields where
needed. Keep adapter code isolated. Maintain a version compatibility matrix.

**Warning signs:**
Adapter code accesses vendor attributes directly without fallback paths or
contract tests.

**Phase to address:**
Phase 2 and Phase 3, during provider adapter implementation.

---

### Pitfall 4: Extracting Too Much Mestre

**What goes wrong:**
The package becomes a clone of Mestre's routing, model policy, benchmark,
optimization, and self-improvement architecture.

**Why it happens:**
The source implementation lives inside Mestre, so product-specific concerns are
near reusable runtime concerns.

**How to avoid:**
Define the package boundary around runtime contracts, adapters, events,
capabilities, and compatibility helpers. Leave routing and optimization in
Mestre.

**Warning signs:**
The package has model ranking, fallback chains, benchmark logic, or database
state before all three adapters are runnable.

**Phase to address:**
Phase 1 and roadmap review.

---

### Pitfall 5: Unsafe Authentication Shortcuts

**What goes wrong:**
The package tries to reuse local account state or scrape credentials across
vendors in unsupported ways.

**Why it happens:**
Local agent tools often have cached user auth, and it is tempting to generalize
that behavior.

**How to avoid:**
Document supported vendor auth paths. Make auth requirements part of
availability diagnostics. Keep unsupported credential scraping out of core.

**Warning signs:**
Code reads vendor auth files or OS keychains directly without explicit vendor
support and user opt-in.

**Phase to address:**
Provider adapter phases.

---

### Pitfall 6: Treating Live Provider Tests as Required CI

**What goes wrong:**
CI becomes flaky, expensive, or impossible for contributors without credentials.

**Why it happens:**
The only realistic proof seems to be actual vendor calls.

**How to avoid:**
Make fake SDK contract tests the default. Provide optional live smoke tests
behind explicit env vars.

**Warning signs:**
The core test suite requires API keys or local Codex/Antigravity setup.

**Phase to address:**
Testing phase.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Copy Mestre files verbatim | Fast start | Public API inherits product-specific names and dependencies. | Only as a temporary spike, not release code. |
| No capability matrix | Simpler docs | Users discover unsupported inputs through failures. | Never for v1. |
| Hard pins to current vendor versions | Fewer surprises today | Blocks users and hides drift risk. | For live smoke test lockfiles, not library install metadata. |
| Synchronous wrapper first | Easier examples | Awkward for streaming and async SDKs. | Add as convenience after async core is stable. |
| One result string only | Simpler API | Loses sessions, usage, tools, parsed output, and errors. | Never for this project. |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Claude Agent SDK | Assuming all option fields exist across versions. | Detect supported constructor fields or pin/test compatibility. |
| Claude Agent SDK | Treating subscription login as a third-party product auth path. | Use supported API/provider auth paths and document limits. |
| Codex SDK | Assuming every OpenAI model is available through the local app-server. | Check app-server model availability and report remediation. |
| Codex SDK | Exposing arbitrary caller-defined tools. | Respect the vendor runtime's tool ownership; expose permissions and sandbox controls. |
| Antigravity SDK | Running from source checkout instead of PyPI wheel. | Install `google-antigravity` from PyPI because wheels include the compiled runtime. |
| Antigravity SDK | Enabling tools/MCP under strict read-only permissions. | Map permissions to capabilities and reject unsafe combinations. |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Storing full event payloads by default | Logs become huge or sensitive. | Truncate previews and make full capture opt-in. | Long-running agent tasks. |
| Blocking stream iteration without idle timeout | Calls hang indefinitely. | Adapter-level first-event and idle timeouts. | Vendor stream stalls. |
| Importing heavy SDKs at startup | CLI/tools feel slow and fail unnecessarily. | Lazy adapter imports. | Multi-provider installs and docs tooling. |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging full prompts, tool args, or file contents | Secret/data leakage. | Redacted/truncated event defaults. |
| Mapping permissive mode too broadly | Unexpected file writes or command execution. | Conservative permission defaults and explicit opt-in. |
| Credential scraping | Unsupported access and user trust risk. | Supported SDK auth only. |
| Silent cwd/workspace expansion | Agent can access more files than intended. | Explicit working directory/workspace behavior in task config. |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| "Provider unavailable" without reason | Users cannot fix setup. | Return diagnostics: missing package, missing auth, unsupported model, missing app-server. |
| Examples that only use one provider | Users do not see the value of the abstraction. | Same-task examples for all three runtimes. |
| Overly clever names for runtime kinds | Hard to map to vendor docs. | Clear runtime ids: `claude`, `codex`, `antigravity`. |
| No capability docs | Users pass unsupported fields and lose time. | Capability matrix in docs and programmatic API. |

## "Looks Done But Isn't" Checklist

- [ ] **Package import:** Core imports without any vendor extras installed.
- [ ] **Extras:** `claude`, `codex`, `antigravity`, and `all` install the expected packages.
- [ ] **Capability errors:** Unsupported MCP/session/schema/tool inputs fail explicitly.
- [ ] **Structured output:** Parsed output is validated or a clear schema error is returned.
- [ ] **Events:** Started, completed, failed, output delta, tool requested/completed, and vendor turn events are covered.
- [ ] **Auth diagnostics:** Missing credentials are not generic runtime errors.
- [ ] **Examples:** Same task can be run through all three adapters.
- [ ] **Mestre path:** There is a documented compatibility plan for later adoption.

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| Public API too Mestre-specific | MEDIUM | Add clean models, keep old names in a compatibility namespace, migrate examples. |
| Vendor SDK drift breaks release | MEDIUM | Add version guard, compatibility branch in adapter, release patch with tested version range. |
| Package name unavailable at publish time | LOW | Re-run naming shortlist and update docs before first release. |
| Hidden unsupported inputs | HIGH | Add capability validation and breaking-change note before public stable release. |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| Hiding vendor differences | Phase 1 | Capability matrix and unsupported-feature tests. |
| Optional dependency failures | Phase 1 | Core import test without extras. |
| SDK drift | Provider adapter phases | Fake SDK API-shape tests plus version matrix. |
| Extracting too much Mestre | Phase 1 and roadmap review | No routing/benchmark/optimization modules in v1 scope. |
| Unsafe auth shortcuts | Provider adapter phases | Auth docs and diagnostics tests. |
| Required live provider tests | Testing phase | CI passes without credentials. |

## Sources

- Mestre runtime protocol: `~/Github/mestre/mestre/vendor_lane/agent_protocol.py`
- Mestre events: `~/Github/mestre/mestre/vendor_lane/events.py`
- Mestre provider adapters: `~/Github/mestre/mestre/vendor_lane/backends/`
- Mestre local-auth design notes: `~/Github/mestre/docs/plans/implemented/RFC_LOCAL_AUTH_RUNTIME.md`
- Claude Agent SDK docs: https://docs.anthropic.com/en/docs/claude-code/sdk
- Codex SDK docs: https://developers.openai.com/codex/sdk
- Google Antigravity SDK README: https://github.com/google-antigravity/antigravity-sdk-python
- PyPI metadata checked for target SDK packages on 2026-06-10.

---
*Pitfalls research for: Python multi-vendor agent runtime SDK*
*Researched: 2026-06-10*
