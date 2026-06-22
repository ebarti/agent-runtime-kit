## Context

agent-runtime-kit is a dependency-light Python package with optional runtime
extras for Claude, Codex, and Antigravity. Its current public contract already
centers on `AgentTask`, `RuntimeRegistry`, runtime adapters,
`PermissionProfile`, output schemas, event sinks, typed `AgentResult` values,
capability declarations, and typed unsupported-feature errors.

The repo also already has a known pressure point: vendor SDKs move faster than
the package lockfile and adapter tests. A local `UV_EXCLUDE_NEWER` cutoff can
hide available upstream SDK releases, so this workflow must explicitly bypass
freshness cutoff environment variables when checking PyPI and lockfile refresh
candidates.

The new agent is both a product feature and a dogfood harness. It should reveal
whether agent-runtime-kit has enough primitives to run real coding-agent
maintenance work without adding model routing, orchestration policy, or
unsupported credential handling to the core library.

## Goals / Non-Goals

**Goals:**

- Provide a local command, initially `python -m examples.sdk_evolution_agent`,
  that researches upstream Claude, Codex, and Antigravity SDK changes and writes
  an evidence-backed report for every run.
- Ensure every AI reasoning, planning, structured-output, implementation, and
  review call is made through agent-runtime-kit runtime primitives.
- Collect deterministic evidence before AI analysis: installed/locked versions,
  newer PyPI versions, metadata, release notes or changelogs, docs/examples,
  importable public module APIs, type signatures, and relevant upstream history
  where available.
- Compare multiple recent versions when useful to distinguish one-off API drift
  from direction-of-travel.
- Classify findings into adapter-only, test-only, docs-only, capability metadata
  change, provider-specific extension, public API evolution, compatibility shim,
  deprecation/migration, architectural rework, or manual-design-required.
- Implement safe local changes only when the evidence and reviewer output are
  sufficiently strong.
- Detect when proposed agent-runtime-kit changes affect the SDK evolution
  agent's own runtime usage, then self-adapt the agent or stop for manual
  design review before leaving it broken.
- Stop with `manual_design_required` when a change implies rearchitecture or
  insufficiently justified public API movement.
- Produce optional draft PRs only when explicitly configured and authenticated;
  never auto-merge.

**Non-Goals:**

- Do not build a generic dependency update bot or scheduled CI workflow first.
- Do not require all vendor SDK extras for normal agent-runtime-kit users.
- Do not hide provider-specific behavior behind a lowest-common-denominator
  abstraction.
- Do not call OpenAI, Anthropic, Google, or other model APIs directly.
- Do not scrape credentials or infer unsupported local auth secrets.
- Do not make PR creation, branch creation, or implementation mandatory for a
  research/report run.

## Decisions

### 1. Build a local example/tool, not a new core orchestrator

Implement the first version as a repo-local module under
`examples/sdk_evolution_agent/` with a thin CLI entry point. The module can use
agent-runtime-kit internally and import test helpers, but it should not expand
the core runtime API unless the design stage proves a real public API gap.

Alternatives considered:

- Add a generic automation framework to `src/agent_runtime_kit`: rejected
  because agent-runtime-kit is a runtime layer, not an orchestrator.
- Build a hidden maintenance script outside examples/docs: rejected because the
  dogfood behavior should be inspectable and useful to community users.

### 2. Separate deterministic evidence collection from AI judgment

Use local deterministic collectors for package and repo facts:

- Read `pyproject.toml`, `uv.lock`, installed distributions, and adapter source.
- Query package metadata and candidate versions for `claude-agent-sdk`,
  `openai-codex`, `openai-codex-cli-bin`, and `google-antigravity`.
- Run targeted refresh previews with cutoff variables removed, especially
  `env -u UV_EXCLUDE_NEWER uv lock --dry-run -P ...`.
- Download or install candidate SDK versions into temporary isolated
  environments for import/API snapshotting instead of mutating the working
  environment.
- Capture public module members, callable signatures, relevant type hints,
  package metadata, examples, docs links, and release-note/changelog references.
- Diff snapshots across current, latest, and selected recent versions.

The AI stages consume these evidence bundles and diffs. They do not discover
facts by guessing from package names.

Alternatives considered:

- Let the coding agent research everything directly with shell/web tools:
  rejected because it makes evidence harder to reproduce and audit.
- Only run `uv lock --dry-run`: rejected because version freshness does not
  explain SDK architectural direction.

### 3. Route all AI stages through agent-runtime-kit

Create a `RuntimeRegistry`, register available adapters, resolve the configured
runtime, and run each AI stage as an `AgentTask`:

- `direction_analysis_task`: consumes the evidence bundle and emits structured
  direction-of-travel analysis.
- `architecture_decision_task`: compares that analysis to current
  agent-runtime-kit APIs and emits a structured architecture decision.
- `implementation_task`: only runs when the decision says safe local
  implementation is allowed; it uses a controlled `PermissionProfile` and
  working directory.
- `review_task`: runs independently through agent-runtime-kit and challenges the
  evidence, inference, architecture scope, provider-specific preservation,
  tests, docs, and migration notes.

Each task uses `AgentTask.output_schema` and consumes `AgentResult.parsed_output`
when supported. The implementation must fail closed when the selected runtime
cannot honor required structured output or permissions instead of silently
falling back to direct model calls.

Alternatives considered:

- Direct SDK/model calls for speed: rejected by the core requirement.
- One monolithic agent task: rejected because reviewer independence and stage
  artifacts are easier to audit when tasks are split.

### 4. Persist event and evidence trails

Implement a JSONL event sink for runtime events and write run artifacts under a
timestamped local report directory, for example
`reports/sdk-evolution/<run-id>/`. A run should include:

- `config.json`
- `evidence.json`
- `api_snapshots/`
- `api_diffs.json`
- `direction_analysis.json`
- `architecture_decision.json`
- `implementation_summary.json`
- `review.json`
- `events.jsonl`
- `report.md`

The report is the primary local deliverable. It must distinguish deterministic
facts, AI inferences, uncertainty, applied changes, test results, and manual
review items.

Alternatives considered:

- Only print terminal output: rejected because reports need to be reviewable and
  PR-ready.
- Persist full credentials/env dumps: rejected because evidence should avoid
  secrets and unsupported credential scraping.

### 5. Use explicit decision gates before editing

The architecture decision must include:

- finding classification,
- confidence and uncertainty,
- evidence references,
- proposed changes,
- required tests/docs/examples/migration notes,
- whether implementation is safe,
- whether `manual_design_required` is true.

Local implementation is allowed only when all of these are true:

- no finding is classified as architectural rework or unresolved public API
  evolution without an accepted migration plan,
- evidence references cover the relevant SDK surfaces,
- the selected runtime supports required output schema and permissions,
- the reviewer does not reject the evidence or architecture decision,
- the user/config explicitly enables implementation.

If these gates fail, the agent writes the report and exits without editing code.

Alternatives considered:

- Always implement whatever the planner proposes: rejected because upstream SDK
  direction often requires human product judgment.

### 6. Keep PR behavior optional and draft-only

The CLI may create or reuse a branch and open/update a draft PR only when
explicit flags/config are set and `gh` authentication is available. The PR body
is generated from the local report and must include evidence, direction
analysis, architecture decision, implementation summary, tests, uncertainty, and
manual review checklist. Auto-merge and release publication are out of scope.

Alternatives considered:

- Scheduled auto-PRs as v1: rejected because the local command and report trail
  need to be reliable first.
- Auto-merge on passing tests: rejected by the safety boundary.

### 7. Add public API only when dogfooding proves a gap

The likely first implementation can live outside the core package. If the agent
exposes gaps, prefer narrowly typed additions:

- richer capability metadata for provider-specific features,
- provider-specific extension points that remain explicit,
- typed unsupported-feature errors for newly discovered task fields,
- compatibility shims where upstream SDK drift is isolated,
- migration notes for any public API evolution.

Do not add generic "provider options" bags that hide semantics or convert every
vendor feature into a weak lowest-common-denominator field.

### 8. Treat runtime-layer changes as recursive compatibility work

The SDK evolution agent depends on agent-runtime-kit to run its own AI stages.
If a proposed change affects `AgentTask`, `AgentResult`, `RuntimeRegistry`,
runtime adapter registration, output schemas, event sinks, permission profiles,
or unsupported-feature errors, the architecture decision must identify the
change as recursive impact.

Recursive impact has an extra gate: before the run is accepted, the agent must
either update its own usage, schemas, tests, and documentation to match the new
runtime contract, or mark `manual_design_required`. This prevents the agent from
successfully adapting the SDK while breaking the tool that performs future
adaptation work.

Alternatives considered:

- Ignore the agent's own compatibility because it is only an example: rejected
  because the agent is explicitly a dogfood harness and should surface real
  runtime API breakage.
- Freeze the agent to an old compatibility shim forever: rejected because that
  would hide whether the current runtime primitives remain sufficient.

## Risks / Trade-offs

- Upstream docs or release notes may be incomplete -> Mitigation: combine PyPI
  metadata, local package API snapshots, examples, docs, and repository history
  where available; mark uncertainty explicitly.
- Importing multiple SDK versions can pollute the environment -> Mitigation:
  perform candidate inspection in temporary isolated environments and never
  mutate the project lockfile unless implementation is explicitly enabled.
- Selected runtime may not support required structured output or permissions ->
  Mitigation: fail closed with a typed unsupported-feature result and document
  runtime requirements.
- AI implementation may overreach -> Mitigation: deterministic gates, scoped
  permissions, review task, tests, and `manual_design_required` stop states.
- Dogfooding can reveal missing agent-runtime-kit primitives -> Mitigation:
  treat that as useful evidence; propose typed API evolution rather than
  stuffing behavior into metadata.
- Recursive self-adaptation can mask public API breakage -> Mitigation: require
  the report to call out recursive impacts separately, update the agent's own
  tests/docs when safe, and stop for manual design when the migration is not
  obvious.
- Draft PR creation can leak weak evidence into review -> Mitigation: draft-only
  PRs, explicit config, report-derived body, manual checklist, and no auto-merge.

## Migration Plan

1. Add the example/tool module and tests without changing the public runtime API.
2. Document local credentials, runtime selection, report outputs, cutoff bypass,
   implementation gates, and optional draft PR flags.
3. Run the command in report-only mode against the current SDK set.
4. If the report identifies safe adapter/test/docs changes, enable local
   implementation and run the targeted verification commands.
5. If public API changes are proposed, require migration notes and keep those
   changes reviewable behind explicit specs/tasks.
6. If public API or runtime-layer changes affect the SDK evolution agent itself,
   update the agent's own runtime usage and tests in the same scoped change or
   stop with `manual_design_required`.
7. Roll back by deleting the generated report directory and reverting any local
   implementation changes; no external service state changes occur unless draft
   PR creation was explicitly enabled.

## Open Questions

- Should the example use only standard-library validation plus
  `AgentTask.output_schema`, or add a dev/test dependency such as `jsonschema`
  for stricter report validation?
- Which runtime should be the default for local runs when multiple adapters are
  available: an explicit required flag, first available runtime, or a config
  file default?
- How much upstream repository history should be fetched by default before the
  run becomes too slow for local use?
- Should generated reports be gitignored by default, or should selected example
  reports be committed as fixtures?
- Should recursive self-adaptation support one-generation migrations only, or
  should the agent be able to replay its report through both old and new runtime
  contracts when the public API changes?
