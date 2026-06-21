## 1. Module and CLI Setup

- [x] 1.1 Create `examples/sdk_evolution_agent/` with a package entry point runnable as `python -m examples.sdk_evolution_agent`.
- [x] 1.2 Add CLI options for runtime kind, provider/package selection, report directory, report-only mode, implementation enablement, branch creation, and optional draft PR creation.
- [x] 1.3 Resolve AI runtimes through `RuntimeRegistry` and built-in adapter registration, with no direct vendor model API clients in the agent code.
- [x] 1.4 Add a JSONL event sink used by every AI `AgentTask` and include the event log in each run directory.

## 2. Structured Models and Schemas

- [x] 2.1 Define structured models or schema dictionaries for evidence bundles, package versions, API snapshots, API diffs, direction analysis, architecture decisions, implementation summaries, reviewer output, and report metadata.
- [x] 2.2 Define output schemas for all AI stages and wire them through `AgentTask.output_schema`.
- [x] 2.3 Add validation helpers that fail closed when required AI structured output is missing or invalid.
- [x] 2.4 Add a shared run context that tracks run id, selected runtime, inspected packages, workspace paths, report paths, and safety flags.

## 3. Upstream Research Collectors

- [x] 3.1 Read current project state from `pyproject.toml`, `uv.lock`, installed distribution metadata when available, and current adapter source files.
- [x] 3.2 Detect current, installed, locked, latest, and selected recent versions for `claude-agent-sdk`, `openai-codex`, `openai-codex-cli-bin`, and `google-antigravity`.
- [x] 3.3 Run targeted SDK refresh previews with `UV_EXCLUDE_NEWER` and similar freshness cutoff variables removed from the command environment.
- [x] 3.4 Collect package metadata, release notes or changelog references, docs links, examples, and upstream repository references when available.
- [x] 3.5 Persist `evidence.json` with source references and clear distinction between deterministic facts and unavailable evidence.

## 4. API Snapshot and Diffing

- [x] 4.1 Implement isolated temporary-environment inspection for candidate SDK versions without mutating the project lockfile or working environment.
- [x] 4.2 Capture public module members, callable signatures, type hints where available, version metadata, and import errors for each inspected SDK version.
- [x] 4.3 Compare current, latest, and selected recent snapshots to produce structured API diffs.
- [x] 4.4 Store snapshots under `api_snapshots/` and write `api_diffs.json` with version and source references.

## 5. Runtime-Driven AI Stages

- [x] 5.1 Implement the direction-analysis `AgentTask` using the selected runtime, evidence bundle, API diffs, output schema, permission profile, working directory, and event sink.
- [x] 5.2 Implement the architecture-decision `AgentTask` that compares upstream direction against agent-runtime-kit's public API, adapter model, capability model, and error model.
- [x] 5.3 Implement the optional implementation `AgentTask` with scoped permissions and a working directory, enabled only after decision gates pass.
- [x] 5.4 Implement the independent reviewer `AgentTask` that challenges evidence sufficiency, inference quality, architecture scope, provider-specific preservation, tests, docs, and migration notes.
- [x] 5.5 Detect recursive self-adaptation impact when proposed runtime-layer changes affect the agent's own use of `AgentTask`, `AgentResult`, `RuntimeRegistry`, adapters, output schemas, event sinks, permission profiles, or typed unsupported-feature errors.
- [x] 5.6 Ensure any runtime inability to honor required output schema, permissions, working directory, or event sink behavior is reported and does not fall back to direct model calls.

## 6. Decision Gates, Implementation, and PR Handling

- [x] 6.1 Enforce report-only behavior by default, with no project edits unless implementation is explicitly enabled.
- [x] 6.2 Enforce `manual_design_required` stop behavior for architectural rework, unresolved public API evolution, broad provider semantics, insufficient evidence, or reviewer rejection.
- [x] 6.3 Enforce recursive self-adaptation gates so runtime-layer changes either update the SDK evolution agent's own usage, schemas, tests, and docs in the same scoped change or stop with `manual_design_required`.
- [x] 6.4 Apply only high-confidence scoped changes tied to evidence, including adapter updates, tests, docs, examples, capability metadata, provider-specific extensions, or compatibility shims.
- [x] 6.5 Run required verification commands and record pass/fail results in `implementation_summary.json`.
- [x] 6.6 Add optional branch creation and draft PR creation/update via local Git/GitHub tooling only when explicitly configured and authenticated.
- [x] 6.7 Ensure draft PR bodies are generated from the local report and include evidence, direction analysis, architecture decision, recursive self-adaptation impact, implementation summary, test results, uncertainty, reviewer output, and manual checklist.
- [x] 6.8 Ensure the agent never auto-merges, auto-publishes, or scrapes unsupported credentials.

## 7. Reports and Documentation

- [x] 7.1 Generate a timestamped local run directory containing config, evidence, snapshots, diffs, AI stage outputs, event logs, implementation summary, reviewer output, and `report.md`.
- [x] 7.2 Document local setup, runtime selection, credential expectations, supported authentication boundary, PyPI cutoff bypassing, report review, implementation gates, and optional draft PR flags.
- [x] 7.3 Update examples or README links so users can discover the SDK evolution agent without installing all vendor extras as mandatory dependencies.
- [x] 7.4 Document how the agent demonstrates dogfooding and where public API gaps should be escalated for manual design.

## 8. Tests and Verification

- [x] 8.1 Add unit tests for version detection and targeted SDK refresh preview environment handling, including removal of `UV_EXCLUDE_NEWER`.
- [x] 8.2 Add tests for evidence bundling with source references and unavailable-source handling.
- [x] 8.3 Add tests for isolated API snapshot collection and API diff generation using fake SDK packages or fixtures.
- [x] 8.4 Add tests for direction-analysis, architecture-decision, implementation-summary, and reviewer-output schema validation.
- [x] 8.5 Add tests proving AI stages construct `AgentTask` values and use `RuntimeRegistry` rather than direct vendor model API calls.
- [x] 8.6 Add tests for recursive self-adaptation detection and for safe self-migration versus `manual_design_required` stop behavior.
- [x] 8.7 Add tests for reviewer rejection and `manual_design_required` blocking implementation.
- [x] 8.8 Add tests for local command behavior in report-only mode and implementation-enabled mode with fake runtime fixtures.
- [x] 8.9 Add tests for report generation and optional draft PR body generation without requiring GitHub authentication.
- [x] 8.10 Run `uv run ruff check .`, `uv run mypy`, and the relevant `uv run pytest` suites for the implemented files.
