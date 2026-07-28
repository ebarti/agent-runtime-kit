# SDK Evolution Demo Reliability Repair Plan

Status: implementation-ready
Written: 2026-07-13
Baseline: `origin/main` at `2e7e4c3` (`v0.5.0`)

## Outcome

Make the SDK evolution demo produce truthful, actionable evidence from the
documented local workflow, including when only the selected runtime extra is
installed. A successful decision run must compare an observed locked SDK
baseline with an observed resolver-selected candidate, report incomplete
evidence as incomplete, and preserve the existing fail-closed implementation
gates.

This plan is intentionally narrower than the historical overhaul in draft PR
#40. It fixes the defects reproduced on current `main` without importing the
older branch's unrelated transaction, resolver, scheduling, or implementation
changes.

## Reproduced behavior

The following behavior was reproduced from a fresh `origin/main` worktree:

1. The focused SDK evolution suite passes (`50 passed`).
2. The full suite passes (`418 passed, 3 skipped`), as do Ruff, mypy, and
   `uv lock --check`.
3. A normal Codex-backed report runs all three structured AI stages and reuses
   one Codex SDK process, but its behavior headline says `pass` while the raw
   Claude and Antigravity baseline probes contain import failures.
4. Adding `--inspect-candidates` installs and probes the candidates, but the
   absent locked SDKs are still probed through the ambient environment. The
   report therefore compares `fail` to `pass`, emits no candidate API diff, and
   is rejected by the deterministic API-diff gate.
5. Installing every vendor extra before the same command produces two valid API
   diffs, passing before/after behavior probes, `safe_to_implement=true`, and a
   passing reviewer decision.
6. The `openai-codex-cli-bin` distribution imports as `codex_cli_bin`; the
   snapshot map currently asks for `openai_codex_cli_bin` and records a false
   import error.
7. The public examples use bare `python -m`. In the fresh worktree that selected
   Python 3.9 despite `.python-version` declaring 3.10, and failed before CLI
   parsing because `dataclasses.field(kw_only=...)` requires Python 3.10.

The Agent SDK execution layer is therefore healthy. The broken boundaries are
locked-baseline evidence collection, behavior aggregation, distribution
introspection, and the operator command contract.

## First-principles model

### Actors and boundaries

- `uv.lock` defines the current dependency baseline.
- The active environment may contain only core plus the selected runtime extra.
- `collect_evidence` records locked, installed, latest, and resolver-selected
  versions.
- API snapshots and behavior probes observe baseline and candidate packages.
- Diff and summary layers convert observations into evidence.
- Deterministic guards decide whether implementation is allowed.
- AI stages interpret the evidence but cannot override deterministic guards.
- Documentation and repo-local runbooks define the supported operator command.

### Invariants

1. A version comparison is valid only when both versions were actually
   observed.
2. The current side of an SDK update comparison is the locked version, not an
   arbitrary ambient import.
3. Missing or skipped evidence is `incomplete`; it is never equivalent to
   `pass` or to a behavioral change.
4. Candidate and isolated-baseline installation remains explicit and runs only
   behind `--inspect-candidates` in a credential-scrubbed environment.
5. A required failed or incomplete behavior probe blocks implementation.
6. The documented command supplies the selected runtime dependency and the
   explicit candidate-inspection consent required to satisfy the gates.
7. The outer `uv run` is locked and cannot rewrite dependency state while
   preparing the environment; the agent's explicit refresh preview remains the
   only freshness path.
8. Vendor-specific surfaces remain explicit; this repair does not flatten
   provider capabilities or change adapter contracts.

### Failure dynamics

The failure is environment-dependent and deterministic. It occurs when a
locked optional SDK is absent or differs from the active environment. It is
masked when all vendor extras happen to be installed, which is why unit tests
and developer environments can appear healthy.

## Five whys and fix layer

1. **Why is the report false-green or non-actionable?** The locked Claude and
   Antigravity baselines are not observed in the normal Codex-only environment.
2. **Why are they not observed?** Isolated baseline probing requires
   `installed_version` to be present and different from `locked_version`; an
   absent installation takes the ambient path.
3. **Why does bad evidence reach the report?** The ambient failure is stamped
   with the locked version, failed snapshots are omitted from diffs, and the
   behavior summary consumes diffs rather than raw probe results.
4. **Why does this happen in the supported workflow?** The runbooks install only
   enough for auth and omit `--inspect-candidates` from the decision and
   implementation commands; public examples also bypass the repository's
   declared Python and lockfile through bare `python -m`.
5. **Why did tests not catch it?** They cover installed-version drift and
   opt-in candidate safety, but not a missing optional baseline, raw failure
   aggregation, or command/document conformance.

The proximal symptom is a contradictory report or a deterministic rejection.
The mechanism is invalid provenance followed by loss of missing/failure state.
The trigger is an absent optional SDK in a runtime-specific environment. The
root cause is the evidence contract across collection, aggregation, and
operator invocation. The fix belongs in those layers because they own what was
observed, how it is classified, and how operators request actionable evidence.

## Stacked delivery model

Every PR is based on the branch immediately above it. Review each PR against its
declared base so only that phase's delta is visible.

| PR | Branch | Base | Scope |
| --- | --- | --- | --- |
| 1 | `agent/sdk-evolution-demo-reliability-plan` | `main` | This plan only |
| 2 | `agent/sdk-evolution-locked-baselines` | PR 1 branch | Observe the locked baseline correctly |
| 3 | `agent/sdk-evolution-evidence-integrity` | PR 2 branch | Make summaries and gates fail closed |
| 4 | `agent/sdk-evolution-cli-bin-snapshots` | PR 3 branch | Fix CLI binary introspection |
| 5 | `agent/sdk-evolution-operator-contract` | PR 4 branch | Make supported commands actionable |

All PRs remain draft until their phase checks and ordinary correctness review
pass. Reviews must not run security-diff scans, per the delivery instruction.

## PR 2: Observe locked baselines correctly

### Production changes

- In `collect_behavior_evidence`, treat `installed_version is None` the same as
  installed-version drift when `locked_version` exists.
- With `--inspect-candidates`, probe that locked version in the existing
  credential-scrubbed isolated environment and label it `current-baseline`.
- Without the opt-in, do not install anything and do not relabel an ambient
  import as the locked baseline. Record only the actual ambient installed
  version (or `None` when absent); PR 3 classifies that unavailable locked
  comparison as incomplete.
- Apply the same missing-or-drifted decision to `_collect_snapshots`: with the
  opt-in, snapshot the locked version in an isolated environment; without it,
  retain only an explicitly unusable current-environment snapshot whose
  version is the observed installed version, not the requested lock.
- Preserve the fast ambient path when installed and locked versions match.
- Convert isolated snapshot venv creation, package installation, execution,
  timeout, and malformed-output failures into bounded structured snapshot
  evidence rather than aborting the report. Preserve package, requested version,
  and the scrubbed-environment boundary; do not copy unbounded subprocess output
  into artifacts. Behavior subprocess error handling lands atomically with its
  summary and guard in PR 3.

### Tests

- Missing installed SDK plus opt-in probes the locked version as
  `current-baseline` before the candidate.
- Missing installed SDK without opt-in performs no isolated install and never
  stamps the locked version onto the ambient result.
- Snapshot collection isolates a missing locked baseline when opted in.
- Snapshot collection performs no isolated install without the flag.
- A matching installed/locked version remains eligible for the ambient path.
  Drifted or absent baselines use `current-baseline` when opted in and remain
  explicitly unusable when the flag is off.
- Assert that isolated baseline and candidate subprocesses retain the scrubbed
  environment contract.
- Snapshot venv creation, install, execution, timeout, and malformed-output
  failures produce an explicit failed snapshot artifact and a completed report.

### Files

- `examples/sdk_evolution_agent/behavior.py`
- `examples/sdk_evolution_agent/snapshots.py`
- `examples/sdk_evolution_agent/cli.py`
- `tests/test_sdk_evolution_agent.py`

### Acceptance

- A core/Codex-only environment with `--inspect-candidates` produces valid
  locked-to-candidate inputs without requiring `[all]`.
- No freshly downloaded package is installed or imported when the flag is off.
- No `current-environment` failure is presented as an observed locked baseline.

## PR 3: Make evidence summaries and gates truthful

### Production changes

- Change behavior summarization to consume raw probe results as well as diffs.
- Introduce one shared resolver-transition parser returning exact
  `(package, from_version, to_version)` tuples and use it for snapshot/behavior
  collection plus deterministic gates. Cover prerelease and multiple-update
  output so duplicated weaker parsers cannot drift.
- Define four report states:
  - `pass`: required observations exist and no contract change is detected.
  - `changed`: required observations exist and only non-breaking change exists.
  - `incomplete`: a required observation was skipped, missing, malformed, or
    failed to execute/import/install.
  - `fail`: a probe completed and proved a required contract failure, or a
    breaking diff exists.
- Use deterministic precedence `fail > incomplete > changed > pass`. A failure
  with `details.missing` is a proven contract failure; `details.error`, skips,
  and missing comparisons are incomplete evidence.
- Preserve existing summary keys and add contract-failure, probe-error, skipped,
  and missing-comparison counts plus concise reasons.
- Persist the additive summary as `behavior_summary.json` and render the same
  counts/reasons in the Markdown report.
- Add `behavior_summary.json` to `current_state.json` artifact references so its
  path and hash participate in the durable baseline manifest.
- Keep one-sided skips out of behavior diffs; absence is not a change.
- Compute missing comparisons only for resolver-selected transitions. A package
  with a passing baseline and no selected update has no required candidate and
  remains eligible for `pass`.
- Convert behavior venv creation, install, probe execution, timeout, and
  malformed-output failures into bounded structured probe evidence. Land this
  with classification and gating so a formerly loud failure can never become an
  intermediate false-green.
- Use one deterministic assessment helper for collection, reporting, and
  gating; never trust a contradictory cached summary over raw results/diffs.
- Extend `with_behavior_probe_guard` so `fail` and `incomplete` both set
  `safe_to_implement=false`, set `manual_design_required=true`, and preserve a
  concrete reason in findings/uncertainty.
- Fail closed when the behavior summary is missing, malformed, or has an unknown
  status.
- Require API-diff evidence to match the exact resolver-selected package,
  locked `from_version`, and candidate `to_version`; package membership alone
  is insufficient.
- Surface API snapshot import/execution errors in `report.md` so a zero diff
  count cannot imply successful inspection.
- Keep `pass` and non-breaking `changed` eligible for later deterministic gates.

### Tests

- A baseline execution/import error with no candidate diff reports `incomplete`,
  not `pass`.
- A completed baseline contract failure reports `fail`.
- Skipped baseline/candidate evidence reports `incomplete`, not `pass`.
- Candidate install/probe execution failure reports `incomplete` and blocks
  implementation; a successfully executed probe with missing required contract
  fields reports `fail`.
- A complete unchanged pair reports `pass`.
- A complete non-breaking changed pair reports `changed` without being treated
  as breaking.
- Status precedence covers fail plus incomplete and changed plus incomplete.
- A passing baseline with no selected update has
  `missing_comparison_count=0`; an expected update with a missing candidate is
  incomplete.
- The Markdown report renders status, failure/skip/error counts, reasons, and
  snapshot errors; `behavior_summary.json` matches it.
- `current_state.json` records the summary artifact's path and hash.
- The behavior guard blocks `fail` and `incomplete`, while allowing `pass` and
  non-breaking `changed` to continue to other gates.
- Missing, non-mapping, unknown-status, and contradictory `pass` summaries with
  raw fail/skip evidence all block deterministically.
- The API guard rejects a diff for the correct package with the wrong from/to
  versions and accepts an exact empty transition diff.
- Shared transition parsing covers multiple packages and prerelease versions.

### Files

- `examples/sdk_evolution_agent/behavior.py`
- `examples/sdk_evolution_agent/collectors.py`
- `examples/sdk_evolution_agent/stages.py`
- `examples/sdk_evolution_agent/report.py`
- `examples/sdk_evolution_agent/current_state.py`
- `docs/sdk-evolution-agent.md`
- `tests/test_sdk_evolution_agent.py`

### Acceptance

- The headline can be derived from the raw artifact without contradiction.
- No empty diff set can hide failed or skipped required observations.
- Deterministic gates, not an AI interpretation, own the fail-closed decision.

## PR 4: Fix Codex CLI binary introspection

### Production changes

- Map `openai-codex-cli-bin` to its real import module, `codex_cli_bin`.
- Keep the distribution name unchanged for package metadata and resolver work.
- Strengthen the binary behavior probe to import `codex_cli_bin`, call
  `bundled_codex_path()`, and prove the bundled executable exists instead of
  treating distribution metadata alone as success.
- Preserve the `binary-distribution` probe name when the isolated embedded probe
  raises; it currently hardcodes `adapter-contract`, which prevents correct
  before/after pairing.
- Do not introduce a special lowest-common-denominator adapter abstraction; the
  snapshot remains package-specific evidence.

### Tests

- Current-environment snapshot imports `codex_cli_bin` for the distribution.
- The isolated snapshot script receives the same module name.
- The real installed locked package produces a snapshot without import error
  when the Codex extra is available.
- The binary behavior probe passes only when the module and bundled path are
  usable, and failures remain labeled `binary-distribution`.
- Existing package mappings remain unchanged.

### Files

- `examples/sdk_evolution_agent/snapshots.py`
- `examples/sdk_evolution_agent/behavior.py`
- `tests/test_sdk_evolution_agent.py`

### Acceptance

- Current reports no longer contain a synthetic CLI-bin import failure.
- A future resolver-selected CLI-bin update can produce a before/after API diff
  instead of being permanently blocked by the wrong module name.

## PR 5: Make the operator workflow actionable

### Documentation and runbook changes

- Update `.codex/skills/agent-runtime-kit-upgrade/SKILL.md` so Codex-backed runs
  use `uv run --locked --extra codex` for auth, report, and implementation
  passes.
- Update `.claude/commands/agent-runtime-kit/upgrade.md` so Claude-backed runs
  use `uv run --locked --extra claude`; document the corresponding Codex and
  Antigravity extras when another runtime is selected.
- Couple runtime and extra selection explicitly everywhere:
  `claude-agent-sdk` uses `--extra claude`, `codex-agent-sdk` uses
  `--extra codex`, and `antigravity-agent-sdk` uses `--extra antigravity`.
  Remove instructions that say to replace only `--runtime`.
- Replace bare public `python -m` commands with `uv run --locked` so commands
  honor the supported Python version and do not rewrite `uv.lock` while setting
  up the outer environment.
- Add `--inspect-candidates` to the actionable report-only decision pass and to
  the implementation pass.
- Update `docs/sdk-evolution-agent.md` and its design companion with complete
  runtime-specific commands, the `incomplete` status contract, and the new
  `behavior_summary.json` artifact.
- Add `behavior_summary.json` to the inspection lists in both repo-owned
  runbooks.
- State explicitly that `--inspect-candidates` installs both a missing/drifted
  locked baseline and resolver-selected candidates in scrubbed environments.
- Preserve the credential-free fake runtime as a pipeline-shape check, not an
  upgrade decision.

### Tests

- Add a small documentation-conformance test that asserts each repo-owned
  runbook's default real-runtime command includes its required extra,
  `uv run --locked`, `--refresh-preview`, and `--inspect-candidates`.
- Assert all three runtime-to-extra mappings.
- Assert implementation examples retain the same inspection flag.
- Reject bare `python -m examples.sdk_evolution_agent` in executable operator
  command blocks while allowing explanatory prose.
- Assert the docs still describe candidate inspection as explicit opt-in and
  never imply that the fake reviewer proves upgrade safety.

### Files

- `.codex/skills/agent-runtime-kit-upgrade/SKILL.md`
- `.claude/commands/agent-runtime-kit/upgrade.md`
- `docs/sdk-evolution-agent.md`
- `docs/sdk-evolution-agent-design.md`
- `tests/test_sdk_evolution_docs.py`

### Acceptance

- Copying the documented Claude, Codex, or Antigravity command into a fresh
  worktree supplies the selected runtime and can satisfy candidate evidence
  gates.
- The command does not require installing every vendor extra.
- Runbooks and public docs agree on opt-in execution, authentication, evidence
  states, and stop gates.

## Cross-stack verification

Run the smallest relevant test set during development, then all of these from
the top branch:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv lock --check
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked ruff check .
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked mypy
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked pytest -q
```

Also rehearse these exact report-only paths in a fresh worktree:

```bash
# Explicitly incomplete discovery pass: candidates exist but inspection is off.
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked python -m examples.sdk_evolution_agent \
    --runtime fake \
    --refresh-preview

# Complete deterministic evidence pass from a core-only environment.
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked python -m examples.sdk_evolution_agent \
    --runtime fake \
    --refresh-preview \
    --inspect-candidates

# Complete real-runtime pass with only the selected runtime extra.
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --inspect-candidates
```

The first command must report incomplete candidate evidence. The second must
produce valid locked/candidate comparisons. The third must complete all
structured stages, reuse the Codex process, and give the reviewer valid
evidence. A deterministic no-update regression test must separately prove that
a baseline-only package does not invent a missing candidate or downgrade.

The live rehearsal remains report-only. It must not enable implementation,
create a branch, push, or open an autonomous upgrade PR.

## Review protocol

For each phase:

1. Review only `base...head` for that PR.
2. Check phase acceptance criteria and tests.
3. Run an ordinary correctness, compatibility, and missing-test review.
4. Do not run a security diff or security scan.
5. Fix material findings on the same phase branch and repeat the targeted
   review before publishing the next layer.
6. Verify live GitHub base/head topology after publication.

## Compatibility and risk controls

- The public `agent_runtime_kit` API is unchanged.
- Candidate inspection stays opt-in and credential-scrubbed.
- The new `incomplete` value appears only in generated example artifacts; any
  downstream parser that assumed three status values must be updated in PR 3.
- No vendor dependency ranges or lockfile versions change in this repair stack.
- No scheduled automation is added.
- No direct vendor model API calls are added.
- Existing open PRs #19 and #40 are not used as stack bases because both carry
  stale ancestry and broader changes. Their eventual disposition is separate
  from this repair stack.

## Completion definition

The stack is complete when all five PRs are published with correct bases, each
phase has passed its targeted tests and non-security review, the top branch
passes the full verification matrix, and a Codex-backed report-only rehearsal
from a runtime-specific environment produces truthful actionable evidence.
