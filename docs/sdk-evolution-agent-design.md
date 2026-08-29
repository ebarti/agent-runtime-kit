# SDK Evolution Agent Design

This document describes how the SDK evolution example should work before adding
more implementation. It is intentionally more detailed than the user-facing run
guide in `docs/sdk-evolution-agent.md`.

The core idea is that a dependency update is not enough evidence. The agent
must combine resolver facts, release notes, API shape, adapter behavior probes,
and real-runtime review before it recommends a lockfile change, adapter change,
or manual design stop.

## Goals

The SDK evolution agent should answer these questions for every run:

- What package versions are installed, locked, and available upstream?
- Which newer upstream releases exist, which are excluded by current bounds,
  and which exact versions are prospectively resolvable together?
- What changed in public API shape?
- What changed in documented behavior or product direction?
- Which adapter behavior contracts still pass on the candidate versions?
- Does the current `agent-runtime-kit` abstraction still preserve vendor
  behavior?
- Is the safe next action a lock update, adapter update, docs/test update,
  provider-specific extension, public API evolution, or manual design review?

The agent must dogfood `agent-runtime-kit`: all AI reasoning stages run through
`AgentTask`, `RuntimeRegistry`, runtime adapters, output schemas, event sinks,
permission profiles, and `AgentResult`. Local shell, filesystem, package
manager, Git, and GitHub operations are allowed only for deterministic evidence
collection and mechanical changes.

## Non-Goals

The example should not become a generic dependency update bot. A generic bot
can answer "can the lockfile move?" This agent must answer "does the runtime
adapter contract still hold, and does the public SDK architecture still make
sense?"

It should not hide vendor differences. If Claude adds task status events, Codex
changes sandbox semantics, or Antigravity changes model endpoint configuration,
the right output is explicit provider-specific evidence and possibly a
provider-specific extension, not a flattened common denominator.

It should not require all vendor SDKs for normal package users. The example can
use `agent-runtime-kit[all]` for local research, but the package itself must keep
optional extras.

## High-Level Flow

```mermaid
flowchart TD
    A["Start local command"] --> B["Collect deterministic evidence"]
    B --> C["Resolve update candidates"]
    C --> D["Inspect current and candidate APIs"]
    D --> E["Collect changelog and release-note evidence"]
    E --> F["Run adapter behavior probes"]
    F --> G["Build evidence bundle"]
    G --> H["Direction analysis through agent-runtime-kit"]
    H --> I["Architecture decision and update plan through agent-runtime-kit"]
    I --> J["Independent review through agent-runtime-kit"]
    J --> K{"Gates pass?"}
    K -- "No" --> L["Write report with manual review checklist"]
    K -- "Yes" --> M["Apply safe implementation"]
    M --> N["Run verification"]
    N --> O["Promote updated state to current baseline"]
    O --> P["Write report and optional draft PR"]
```

Step responsibilities:

- **Start local command**: Parse the selected runtime, package filters, report
  directory, refresh options, implementation flag, branch option, and draft PR
  option. This step also establishes the run ID and local report directory.
- **Collect deterministic evidence**: Read local project state without using AI:
  `pyproject.toml`, `uv.lock`, installed distributions, package metadata,
  configured source hints, local environment facts, and supported auth
  availability. This produces raw facts, not recommendations.
- **Resolve update candidates**: Build the candidate inventory from independent
  upstream package metadata before consulting current constraints. Run both the
  current targeted resolver preview and, when an upper bound excludes a newer
  release, a no-mutation prospective preview in a temporary manifest with only
  the excluding upper bound widened. Resolver output classifies feasibility; it
  must not define away candidates hidden by the current manifest.
- **Inspect current and candidate APIs**: Treat `uv.lock` as the baseline and
  inspect every exact compatible candidate. Reuse one credential-scrubbed
  package/version environment for its snapshot and behavior probe. If an update
  candidate has no exact candidate API diff, the run must not proceed to
  implementation.
- **Inspect recent implementations**: When explicit candidate inspection is
  enabled, inspect the three most recent published releases independently of
  whether an update is pending. Fingerprint shipped files and Python AST
  definitions, compare adjacent releases, and preserve opaque-artifact
  limitations. This evidence is longitudinal analysis, not resolver input.
- **Collect changelog and release-note evidence**: Fetch or read official
  changelogs, release pages, docs changelogs, repository releases, and package
  metadata links. This step records what changed according to the vendor and
  explicitly marks missing or incomplete release-note coverage.
- **Run adapter behavior probes**: Execute deterministic unit probes, installed
  SDK contract probes, and optional live probes. This step answers whether the
  adapter behavior still holds, including permissions, sandbox/workspace
  handling, streaming, structured output, MCP/tool support, auth discovery, and
  session/resume behavior.
- **Build evidence bundle**: Normalize package facts, resolver facts, API
  snapshots, API diffs, release-note evidence, behavior probe results, source
  references, and uncertainty into a compact bundle for the AI stages. This step
  should preserve provenance so later reasoning can be traced back to evidence.
- **Implementation-trend analysis through agent-runtime-kit**: Ask a runtime,
  via `AgentTask`, to infer implementation patterns from exact recent-release
  file and definition diffs, corroborated by API, behavior, and release-note
  evidence. This step identifies whether changes look isolated or part of a
  broader SDK implementation direction. It must not recommend upgrade, hold,
  resolver, or lockfile actions; it does not own the implementation plan.
- **Architecture decision and update plan through agent-runtime-kit**: Ask a
  runtime, via `AgentTask`, to turn direction analysis into the concrete plan:
  adapter-only, test-only, docs-only, capability metadata change,
  provider-specific extension, public API evolution, compatibility shim,
  deprecation/migration, architectural rework, or `manual_design_required`.
  This is the step responsible for saying what should be updated.
- **Independent review through agent-runtime-kit**: Run a separate reviewer task
  through the runtime. The reviewer challenges evidence sufficiency, direction
  inference, plan scope, vendor-specific capability preservation, and whether
  tests, docs, and migration notes match the proposed change.
- **Gates pass?**: Apply deterministic pass/fail rules. The gates block
  implementation when required API diffs are missing, release-note coverage is
  missing, behavior probes fail or are skipped for required contracts, the
  reviewer rejects the plan, recursive self-adaptation is unresolved, or manual
  design is required.
- **Write report with manual review checklist**: If gates fail, write the local
  report with the evidence bundle, analysis, decision, reviewer output,
  uncertainty, blocked reasons, and the exact manual review questions. This is a
  valid end state, not a failed run.
- **Apply safe implementation**: The built-in deterministic implementation lane
  widens only excluding dependency upper bounds, refreshes the lock to the exact
  inspected compatible versions, and rolls both files back on mismatch or
  verification failure. Adapter, public API, test, or documentation changes are
  scoped coding work triggered by a blocked report; they require regression
  evidence and a green rerun before the dependency lane proceeds.
- **Run verification**: Run the verification commands required by the
  architecture decision. At minimum, this should cover formatting/linting,
  typing, unit tests, lock checks, report generation checks, and any available
  live smoke needed for the affected runtime behavior.
- **Promote updated state to current baseline**: After implementation and
  verification pass, record the updated lock/package/API/release-note/probe state
  in `current_state.json`, tied to the verified workspace state. Failed, blocked,
  or manual-design-required runs are never marked promoted.
- **Write report and optional draft PR**: Write the final local report with
  evidence, decisions, implementation summary, baseline-promotion result, test
  results, uncertainty, and manual checklist. If explicitly configured and
  authenticated, create or update a draft PR. This step must never auto-merge.

Every box before implementation-trend analysis is deterministic. AI stages may interpret
evidence, but they should not invent evidence that was not collected.

## Operating Modes

The default command should be report-only:

```bash
uv run --locked python -m examples.sdk_evolution_agent \
  --runtime fake \
  --refresh-preview
```

This mode collects evidence, writes artifacts, runs the analysis stages through
the selected runtime, and stops before editing the workspace. The fake runtime
is allowed only as a deterministic pipeline-shape harness. It proves the
pipeline and schemas, not upgrade safety or the quality of AI reasoning. When
updates exist, the default no-inspection run records incomplete candidate
evidence.

A real analysis run should select one configured runtime:

```bash
uv run --locked --extra claude python -m examples.sdk_evolution_agent \
  --runtime claude-agent-sdk --refresh-preview --inspect-candidates
uv run --locked --extra codex python -m examples.sdk_evolution_agent \
  --runtime codex-agent-sdk --refresh-preview --inspect-candidates
uv run --locked --extra antigravity python -m examples.sdk_evolution_agent \
  --runtime antigravity-agent-sdk --refresh-preview --inspect-candidates
```

Before a Codex-backed run, prepare the dedicated SDK evolution auth home:

```bash
env -u UV_EXCLUDE_NEWER \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

This helper owns the operator-readiness boundary for Codex-backed runs. It
creates `~/.codex_agent_runtime_sdk`, checks supported Codex CLI login status for
that exact home, and mirrors the supported normal Codex login cache from
`~/.codex/auth.json` when that cache is newer. It must not parse credentials,
ask for access tokens, or inspect unsupported credential stores.

When `codex-agent-sdk` is selected for SDK update work, every AI-backed stage
should run on `gpt-5.5` with `reasoning_effort=xhigh`. This is a Codex runtime
policy, not a portable metadata field: Claude and Antigravity runs should not
receive a `gpt-5.5` model override.

Package filters narrow evidence collection for debugging, but normal evolution
runs should inspect all tracked packages:

```bash
uv run --locked --extra antigravity python -m examples.sdk_evolution_agent \
  --runtime antigravity-agent-sdk \
  --refresh-preview \
  --inspect-candidates \
  --package claude-agent-sdk \
  --package openai-codex \
  --package openai-codex-cli-bin \
  --package google-antigravity
```

`--inspect-candidates` was originally envisioned as effectively always on,
since update candidates without candidate API snapshots are not actionable.
The shipped behavior deliberately inverts that default: candidate inspection
installs and imports freshly downloaded upstream code, so it is opt-in and
runs in a credential-scrubbed environment. The same explicit flag is required
to install a locked baseline that is missing or drifted in the active
environment. When inspection is off, those gaps become explicit `skip` records
rather than claimed observations.

Implementation mode should remain explicitly gated:

```bash
uv run --locked --extra antigravity python -m examples.sdk_evolution_agent \
  --runtime antigravity-agent-sdk \
  --refresh-preview \
  --inspect-candidates \
  --implementation-enabled
```

Even in implementation mode, deterministic gates decide whether edits are
allowed. Draft PR creation is separate and should only happen when the local Git
and GitHub environment is authenticated and explicitly configured with
`--draft-pr`.

## Evidence Layers

The report should clearly separate evidence layers. Mixing them together is how
bad conclusions slip in.

### 1. Package and Resolver Evidence

The agent checks:

- `pyproject.toml` dependency declarations.
- `uv.lock` versions.
- Installed distributions in the local environment.
- Independent PyPI latest and recent-release metadata.
- Current constrained and prospective temporary-manifest
  `uv lock --dry-run --exclude-newer false -P ...` output.

Upstream metadata is the source of truth for whether a newer direct SDK release
exists. Prospective resolution is the source of truth for the compatible set
that can be applied. For `openai-codex-cli-bin`, the compatible candidate is the
exact version required by the Codex SDK candidate; a newer standalone CLI
release remains visible but is classified as blocked by that coupling.

### 2. API Shape Evidence

The agent treats the lockfile as the current SDK baseline. If the active Python
environment has drifted from `uv.lock`, it inspects the locked baseline in an
isolated virtualenv instead of using the installed package. Each run captures
fresh evidence for every selected package and every independently discovered
compatible candidate. A package/version environment is cached only within the
run, so its API snapshot and behavior probe reuse one exact install.

For importable packages, snapshots record:

- public member names,
- member kind,
- signature where Python introspection can provide one,
- defining module,
- import errors.

This catches obvious adapter risks:

- removed classes or functions,
- changed constructor signatures,
- changed enum or model surfaces,
- new provider-specific capabilities worth exposing.

API shape is necessary but insufficient. It does not prove behavior.

After a successful implementation, `uv.lock` becomes the next run's
authoritative baseline and `current_state.json` records the evidence that was
accepted.

### 3. Changelog and Release-Note Evidence

The agent should collect release-note context when a vendor publishes it.

| Package | Preferred source | Why it matters |
| --- | --- | --- |
| `claude-agent-sdk` | Python SDK `CHANGELOG.md` and Claude Agent SDK docs | Claude often ships behavioral changes around task progress, sessions, tools, permissions, and model support. |
| `openai-codex` | Codex SDK docs, Codex changelog, and `openai/codex` releases | Codex changes can involve sandboxing, working directories, remote execution, app-server behavior, and SDK maturity. |
| `openai-codex-cli-bin` | `openai/codex` releases and package metadata | The binary package is runtime infrastructure, so behavior can change even when the Python SDK surface does not. |
| `google-antigravity` | Antigravity GitHub Discussions release notes, repository, package metadata, examples, and public API snapshots | Antigravity publishes package release notes as GitHub Discussions announcements. The agent should prefer the GitHub GraphQL Discussions API when a supported token is present, then fall back to public discussion pages before source coverage and uncertainty. |

The report should preserve source references and a short excerpt or summary. If
release notes are unavailable, that absence is evidence and should increase
uncertainty.

Primary sources should be recorded with URLs in `release_notes.json`:

- `claude-agent-sdk`: `https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md`
- Claude Agent SDK docs: `https://code.claude.com/docs/en/agent-sdk/overview`
- Codex SDK docs: `https://developers.openai.com/codex/sdk`
- Codex changelog: `https://developers.openai.com/codex/changelog`
- Codex repository releases: `https://github.com/openai/codex/releases`
- Antigravity release notes: `https://github.com/google-antigravity/antigravity-sdk-python/discussions/categories/announcements`
- Antigravity v0.1.5 release notes: `https://github.com/google-antigravity/antigravity-sdk-python/discussions/87`
- Antigravity repository: `https://github.com/google-antigravity/antigravity-sdk-python`

If a package has no release-note source for the exact version interval, the
agent should still record what it checked and why the source was insufficient.
Fetched official sources with no package-version-specific entry are evidence
with explicit uncertainty; they are not the same as a collection failure.

### 4. Behavior Probe Evidence

Behavior probes test what signatures cannot show. They should be deterministic
where possible and optional-live where credentials are required.

```mermaid
flowchart LR
    A["Candidate versions installed"] --> B["Contract tests"]
    A --> C["Adapter unit probes"]
    A --> D["Optional live smoke"]
    B --> E["behavior_probes.json"]
    C --> E
    D --> E
    E --> F["Architecture decision gates"]
```

Behavior probes should cover these contracts:

| Contract | Why API diffs are not enough | Example probe |
| --- | --- | --- |
| Request construction | Signatures can stay stable while validators or field meaning changes. | Construct the exact SDK options/config object with adapter-owned values; Antigravity includes provider-compatible mapped conversation IDs. |
| Permission mapping | Permission mode names can stay present while policy behavior changes. | Strict/default/permissive tests for each adapter. |
| Sandbox and workspace semantics | Behavior can shift across SDK or CLI layers without a Python signature change. | Codex sandbox enum and run argument contract tests, plus smoke where possible. |
| Streaming and event order | New message types may not break imports but may be dropped. | Feed fake vendor messages and assert emitted event order. |
| Structured output | Schema fields can exist but runtime may return prose or tool calls. | Live or fake structured-output task with schema validation. |
| Session/resume | Resume options can exist but behavior may change. | Fake SDK request shape plus optional live resume smoke. |
| MCP/tool support | MCP config may move from one module to another without a simple signature break. | Adapter MCP config tests and unsupported-feature assertions. |
| Auth discovery | Supported auth sources differ by vendor and may change independently. | Availability probes that report source without scraping credentials. |

Behavior probe output should be a first-class report artifact, for example:

```text
behavior_probes.json
behavior_diffs.json
behavior_summary.json
```

Each probe result should include:

- probe name,
- relevant package or adapter,
- command or test function,
- pass/fail/skip status,
- stdout/stderr summary,
- skipped reason when optional credentials are missing.

`behavior_diffs.json` compares observed locked-baseline probes against
candidate-version probes for independently inventoried compatible updates. Breaking candidate
probe changes block implementation deterministically before any local lock
update.

`behavior_probes.json` may include observed SDK fields or parameters that are
not part of the adapter contract. `behavior_diffs.json` compares the required
adapter contract, not every optional field. Public API and signature churn
remains visible in `api_diffs.json` and probe details, but it should only block
implementation when the required behavior contract fails or becomes ambiguous.
`behavior_summary.json` is the deterministic hand-off: `pass` means complete
unchanged evidence, `changed` means complete non-breaking evidence,
`incomplete` means required observations could not be proved, and `fail` means
a required contract failed or a breaking diff was observed. Missing, malformed,
or unknown summary states also block implementation.

### 5. Runtime-Generated Analysis

After deterministic evidence is collected, the AI stages can interpret it:

```mermaid
sequenceDiagram
    participant CLI as Local CLI
    participant Registry as RuntimeRegistry
    participant Runtime as Selected runtime adapter
    participant Model as Vendor agent runtime

    CLI->>Registry: resolve(runtime kind)
    Registry->>Runtime: create adapter
    CLI->>Runtime: AgentTask(direction-analysis)
    Runtime->>Model: supported SDK call
    Model-->>Runtime: structured AgentResult
    Runtime-->>CLI: validated JSON
    CLI->>Runtime: AgentTask(architecture-decision)
    Runtime-->>CLI: validated JSON
    CLI->>Runtime: AgentTask(review)
    Runtime-->>CLI: validated JSON
```

The AI stages should receive compacted, source-referenced evidence. They should
not be asked to inspect the filesystem directly during report-only analysis.
The direction stage receives implementation diffs as primary evidence. Its
structured package status is `observed`, `opaque-runtime`, `no-transition`, or
`unavailable`; package freshness and resolver status are not implementation
trends. A deterministic postcondition replaces operational advice if a runtime
returns it in a trend field.

## Decision Gates

The agent should fail closed. Implementation is blocked when:

- independent discovery finds a candidate but the prospective no-cooloff
  resolver preview is missing or failed,
- an exact candidate API diff is missing,
- release notes exist but were not collected,
- release notes are unavailable and the API or behavior evidence is ambiguous,
- behavior probes fail,
- behavior probes are skipped for a contract that is required for the proposed
  implementation,
- the reviewer rejects the evidence or architecture decision,
- `manual_design_required` is true,
- recursive self-adaptation is required but no migration plan exists.

An empty API diff can be valid. A missing API diff for an update candidate is
not valid.

## Recursive Self-Adaptation

The SDK evolution agent uses `agent-runtime-kit` to update `agent-runtime-kit`.
That makes runtime-layer changes recursive.

```mermaid
flowchart TD
    A["Upstream SDK change"] --> B["agent-runtime-kit adapter/public API change"]
    B --> C{"Does SDK evolution agent use the changed contract?"}
    C -- "No" --> D["Normal adapter/public API change"]
    C -- "Yes" --> E["Self-adaptation required"]
    E --> F["Update example runtime usage"]
    E --> G["Update schemas and prompts"]
    E --> H["Update behavior probes"]
    E --> I["Run reviewer through updated runtime"]
```

If a change affects `AgentTask`, `AgentResult`, `RuntimeRegistry`, runtime
adapters, output schemas, event sinks, permission profiles, or typed unsupported
feature errors, the report must call this out explicitly.

## Changelog Source Strategy

The agent should prefer official and primary sources:

- package repository changelog files,
- official release pages,
- official docs changelog pages,
- package metadata links,
- repository releases.

It should not scrape private credentials or authenticated browser sessions to
obtain changelogs. If a source requires authentication, the report should mark
that source unavailable and explain the limitation.

For `claude-agent-sdk`, the Python changelog should be checked first. Claude
Code and Agent SDK docs are useful supplemental direction-of-travel sources.

For `openai-codex`, the Codex SDK docs and Codex changelog should be checked.
The `openai/codex` release page is also relevant because the Python SDK depends
on a bundled or pinned runtime.

For `google-antigravity`, check the GitHub Discussions Announcements category
for versioned SDK release-note threads before falling back to repository and
package metadata. Prefer GitHub GraphQL when `GITHUB_TOKEN` or `GH_TOKEN` is
explicitly available; otherwise use the public discussion pages. If no
package-version-specific discussion or repository entry exists, the agent should
not pretend the source is complete. It should compensate with package metadata,
examples, API snapshots, adapter contract tests, and live smoke where credentials
are available.

## Behavior Probe Strategy

Behavior probes should be split into three tiers.

### Tier 1: Always-On Unit Probes

These use fake SDK objects and do not require credentials. They should run in
normal CI.

Examples:

- Claude request shape and stream translation tests.
- Codex approval mode, sandbox, thread item, and tool audit tests.
- Antigravity permission/tool/MCP config tests.
- unsupported-feature errors for non-portable options.

### Tier 2: Installed SDK Contract Probes

These introspect real installed SDK packages but do not call models.

Examples:

- `ClaudeAgentOptions` still accepts fields the adapter builds.
- `openai_codex.AsyncThread.run` still exposes expected parameters.
- `google.antigravity.LocalAgentConfig` still exposes expected config fields.

These are stronger than raw public snapshots because they encode adapter
assumptions.

### Tier 3: Optional Live Probes

These use local supported credentials and must never scrape credentials.

Examples:

- Claude one-turn smoke if Claude auth is configured.
- Codex one-turn smoke using provider-owned local auth.
- Antigravity structured-output smoke using API key or Google Application
  Default Credentials.

Live probes should be reported as pass/fail/skip. A skipped live probe should
not automatically block a docs-only or test-only change, but it should increase
uncertainty for runtime behavior changes.

## Report Shape

The report directory should include:

```text
config.json
evidence.json
release_notes.json
api_snapshots/
api_diffs.json
implementation_snapshots/
implementation_diffs.json
behavior_probes.json
behavior_diffs.json
behavior_summary.json
current_state.json
direction_analysis.json
architecture_decision.json
implementation_summary.json
review.json
events.jsonl
report.md
```

`report.md` should summarize:

- package and resolver status,
- release-note coverage,
- API diff count and affected packages,
- behavior probe status,
- current-state baseline promotion status,
- upstream implementation patterns across exact release intervals,
- architecture decision,
- reviewer status,
- implementation result,
- uncertainty and manual review checklist.

`current_state.json` should be the manifest that makes the next run
artifact-aware. It should record:

- evidence schema version,
- generated timestamp,
- source run ID,
- commit SHA or explicit dirty-worktree marker,
- lockfile hash,
- package names and accepted current versions,
- paths or content hashes for current API and implementation snapshots,
- paths or content hashes for release-note evidence,
- paths or content hashes for behavior probe results,
- a path or content hash for the deterministic behavior summary,
- whether the baseline was promoted, refreshed, skipped, or blocked.

Promotion rules should be conservative:

- promote only after implementation and verification pass,
- do not promote failed, blocked, report-only, or manual-design-required runs as
  the new current state,
- preserve the previous baseline so a bad promotion can be inspected,
- refresh the current-state baseline when the evidence schema changes, even if
  package versions did not change,
- make the final report say exactly which artifacts became the new baseline.

## Caveats and Concerns

Changelogs are incomplete. They often omit small behavior changes and may lag
package releases.

Public API snapshots are shallow. Implementation-history fingerprints add
file-level and Python-definition evidence, but hashes still cannot explain
behavior encoded in runtime binaries, generated models, callbacks, subprocesses,
environment variables, or remote services. Opaque artifacts must remain an
explicit limitation rather than being reverse-inferred from version numbers.

Live probes are environment-sensitive. They prove that one local credential and
runtime setup worked at one time. They do not replace unit or contract probes.

AI review can be overconfident or overcautious. The reviewer should challenge
evidence quality, but deterministic gates should own pass/fail decisions for
missing diffs, failed probes, and missing required release-note evidence.

Provider release cadence differs. Claude may expose rich changelogs. Codex may
split behavior between SDK docs, changelog, GitHub releases, and CLI runtime.
Antigravity may expose less written release context.

Prerelease handling matters. Resolver output should drive update candidates
because package metadata `latest` can point to a stable release while the lock
already contains a newer prerelease.

## Alternatives Considered

### API Diffs Only

Rejected. API diffs catch import and signature drift, but they do not prove
behavioral compatibility. This is the current weak point.

### Changelogs Only

Rejected. Changelogs are useful direction evidence, but they are not complete
and cannot prove local adapter behavior.

### Run Full Live Agents For Every Provider Every Time

Rejected as the default. It is too credential-dependent and would make local
runs brittle. Live probes should be optional and reported clearly.

### Dependabot-Style Lock Updates

Rejected. The goal is architectural evolution, not generic dependency freshness.
The agent must reason about provider-specific runtime capabilities and adapter
contracts.

### Lowest-Common-Denominator Runtime Abstraction

Rejected. The package exists to provide a clean Python API while preserving
vendor-specific capabilities, not to erase them.

### Separate Agents Per Provider Only

Partially useful but not sufficient. Provider-specific probes are valuable, but
the top-level agent still needs a cross-provider architecture view so public API
changes do not accidentally favor one runtime and flatten another.

## Implemented Artifact Contract

The example implements the deterministic evidence artifacts described above:

- `release_notes.json` records official source checks and whether matching
  version evidence was found, missing, or unavailable.
- `behavior_probes.json` records current and candidate adapter-contract probes.
- `behavior_diffs.json` records behavior differences between current and
  candidate probes.
- `behavior_summary.json` records the recomputed deterministic status, counts,
  and reasons handed to implementation guards and operators.
- `current_state.json` records the run baseline, lockfile hash, accepted
  package versions, artifact hashes, and promotion status.

The implementation path is gated by deterministic checks before the local
manifest/lock update runs. A missing or failed prospective resolver preview,
missing candidate API diffs, unavailable required
release-note evidence, behavior summaries that are failed, incomplete, missing,
malformed, or unknown, reviewer rejection, `manual_design_required`, and
unresolved recursive self-adaptation all block implementation. When
implementation is allowed, the example widens only excluding direct dependency
bounds, applies the exact prospectively selected SDK set with freshness cutoffs
disabled, updates the compatibility manifest to the exact tested SDK and
coupled-runtime versions, verifies the resolved versions, and runs lint, typing,
tests, and lock checks. It restores the project manifest, lockfile, and
compatibility manifest on mismatch or verification failure. Only explicitly
requested draft-PR mode may stage the reported changed paths, commit, push, and
open a PR, and only after a verified non-empty update.
