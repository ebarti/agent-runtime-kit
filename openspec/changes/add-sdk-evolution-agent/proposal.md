## Why

Vendor agent SDKs for Claude, Codex, and Antigravity are changing quickly enough
that lockfile refreshes and adapter contract tests are not sufficient. The
project needs a local AI engineering agent that can research upstream SDK
movement, distinguish isolated drift from architectural direction, and adapt
agent-runtime-kit while dogfooding agent-runtime-kit as the runtime layer for
all AI reasoning.

## What Changes

- Add a local SDK evolution agent runnable from the repo, for example
  `python -m examples.sdk_evolution_agent`.
- Build the agent on `AgentTask`, `RuntimeRegistry`, runtime adapters,
  permission profiles, output schemas, event sinks, and structured
  `AgentResult` values.
- Enforce that all AI reasoning, planning, implementation prompts, structured
  output, and independent review are executed through agent-runtime-kit runtime
  primitives rather than direct vendor model API calls.
- Add deterministic research stages for upstream version detection, PyPI cutoff
  bypassing, SDK package inspection, API snapshots, API diffs, documentation
  references, and local state capture.
- Add structured AI stages for direction-of-travel analysis, architecture fit
  review, design proposals, safe implementation planning, and independent
  reviewer challenges.
- Add guarded implementation behavior for high-confidence adapter, metadata,
  tests, docs, examples, and compatibility-shim updates.
- Add recursive self-adaptation handling so changes to agent-runtime-kit that
  affect the SDK evolution agent's own runtime layer are detected, validated,
  and either migrated in the same run or stopped for manual design review.
- Add explicit stop behavior for architectural rework or ambiguous upstream
  direction by marking `manual_design_required`.
- Add local reports for every run, plus optional draft PR creation only when
  explicitly configured and authenticated.
- Document how to run and review the local agent with local credentials and
  without unsupported credential scraping.

## Capabilities

### New Capabilities

- `sdk-evolution-agent`: Local dogfooded agent for researching upstream vendor
  SDK evolution, assessing architecture fit, safely adapting agent-runtime-kit,
  and producing evidence-backed reports or optional draft PRs.

### Modified Capabilities

- None.

## Impact

- Adds repo-local agent code, likely under `examples/sdk_evolution_agent/` or a
  similarly reviewable package/example boundary.
- Adds schemas and data models for evidence bundles, API snapshots/diffs,
  direction analysis, architecture decisions, reviewer output, reports, and PR
  metadata.
- Adds tests for version detection, `UV_EXCLUDE_NEWER` bypassing, evidence
  bundling, API diffing, schemas, reviewer gates, recursive self-adaptation
  checks, command behavior, and report or PR generation.
- May add capability metadata or provider-specific extension points if the
  implementation proves the current public API cannot represent vendor-specific
  SDK direction without flattening it.
- Updates docs with local-run instructions, credential expectations, report
  review workflow, and draft-PR safety boundaries.
