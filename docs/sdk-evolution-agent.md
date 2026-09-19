# SDK Evolution Agent

The SDK evolution agent is a local dogfood workflow for keeping
agent-runtime-kit aligned with Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK as those upstream packages evolve.

For a complete upgrade, invoke the repository's
[`agent-runtime-kit-upgrade` skill](../.codex/skills/agent-runtime-kit-upgrade/SKILL.md).
It collects evidence first, maps upstream implementation trends to the current
integration, implements the necessary adapter/runtime changes with tests and
docs, applies the compatible dependency update, and creates a verified regular
pull request. Explicit report-only and local-only requests retain those limits.
The CLI examples below describe the underlying runner; its report-only default
and dependency-only implementation lane are stages within that broader skill.

For the intended architecture, evidence contract, behavior probe strategy,
changelog strategy, caveats, and alternatives, see
[`docs/sdk-evolution-agent-design.md`](sdk-evolution-agent-design.md).

Run it from the repository:

```bash
uv run --locked python -m examples.sdk_evolution_agent --runtime fake
```

The `fake` runtime is deterministic and useful for checking the local pipeline
without credentials. It proves pipeline shape only, never upgrade safety. When
updates exist, its default no-inspection run records incomplete candidate
evidence. For real AI reasoning, select a configured runtime and its matching
uv extra:

```bash
uv run --locked --extra claude python -m examples.sdk_evolution_agent \
  --runtime claude-agent-sdk --refresh-preview --inspect-candidates
uv run --locked --extra codex python -m examples.sdk_evolution_agent \
  --runtime codex-agent-sdk --refresh-preview --inspect-candidates
uv run --locked --extra antigravity python -m examples.sdk_evolution_agent \
  --runtime antigravity-agent-sdk --refresh-preview --inspect-candidates
```

Every AI-backed stage is dispatched as an `AgentTask` through a runtime resolved
from `RuntimeRegistry`. The agent does not call OpenAI, Anthropic, Google, or
other model APIs directly for reasoning, planning, implementation, review, or
structured output.

When `--runtime codex-agent-sdk` is selected, the agent injects
`CODEX_HOME=~/.codex_agent_runtime_sdk` into the Codex SDK subprocess. This keeps
the dogfooded SDK evolution agent's Codex state separate from a user's normal
Codex home while still using supported Codex authentication mechanisms. The
directory is created with private permissions before the Codex runtime starts.
Run the auth preflight before real Codex-backed SDK evolution runs:

```bash
env -u UV_EXCLUDE_NEWER \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

The helper checks `codex login status` against the dedicated home and can refresh
that home by copying the normal `~/.codex/auth.json` cache when it is newer. If
the helper reports that the isolated home is not authenticated, refresh the
normal Codex login cache and rerun the helper:

```bash
uv run --locked --extra codex codex login --device-auth
env -u UV_EXCLUDE_NEWER \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

Codex-backed SDK evolution runs default to `gpt-5.5` with
`reasoning_effort=xhigh` for the AI stages that analyze direction, decide the
update plan, and review the result. Dependency application and verification are
deterministic. This model policy
is applied only to `codex-agent-sdk`; Claude and Antigravity runs keep their
provider-native model selection because `gpt-5.5` is not a valid model override
for those adapters.
Use `--model` and `--reasoning-effort` to select the requested model explicitly
for every AI stage. For example, use `--model gpt-6-astra --reasoning-effort xhigh`.
These options populate the first-class `AgentTask` fields and are recorded in
`config.json`; unsupported provider values remain provider errors.

If the locked Codex CLI cannot run the requested analysis model, the optional
`--codex-bin /absolute/path/to/codex` selects an installed executable through
the SDK's supported `CodexConfig.codex_bin`. It applies only to the analysis
driver and is recorded separately in `config.json`. Exact baseline/candidate
inspection and the SDK/CLI dependency coupling still use the published package
versions. Omit this override for the required post-update run against the final
locked SDK and bundled CLI. The override requires `--runtime codex-agent-sdk`
and cannot be combined with an injected runtime or registry.
Vendor runtimes also enable `reuse_process=True` for the multi-stage run, so
compatible SDK subprocesses can stay warm across analysis/review stages and are
closed by the CLI when the internally owned runtime exits. Codex reuses its
app-server process, Claude reuses its `ClaudeSDKClient` process, and Antigravity
keeps task isolation unless a stage provides an explicit conversation id.

For Antigravity, local auth can use `GEMINI_API_KEY` / `GOOGLE_API_KEY` or
Google Application Default Credentials. ADC runs use Vertex AI config; provide a
project through ADC, `GOOGLE_CLOUD_PROJECT`, or `GCLOUD_PROJECT`, and optionally
set `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_REGION`, or `CLOUD_ML_REGION`.
Report-only analysis and review stages allow only Antigravity's `finish` tool so
the runtime can return structured output without inspecting the workspace after
the deterministic evidence bundle has already been collected.

## What It Produces

Each run writes a timestamped directory under `reports/sdk-evolution/` with:

- `config.json`
- `evidence.json`
- `release_notes.json`
- `api_snapshots/`
- `api_diffs.json`
- `implementation_snapshots/`
- `implementation_diffs.json`
- `behavior_probes.json`
- `behavior_diffs.json`
- `behavior_summary.json`
- `current_state.json`
- `direction_analysis.json`
- `architecture_decision.json`
- `implementation_summary.json`
- `review.json`
- `events.jsonl`
- `report.md`

The report separates deterministic facts from runtime-generated analysis and
calls out uncertainty, release-note coverage, API diffs, implementation trends, behavior diffs,
baseline promotion, recursive self-adaptation impact, implementation status,
test results, reviewer output, and manual review items.

## Upstream Freshness

The agent checks the current project state in `pyproject.toml`, `uv.lock`, and
installed distributions, then compares it with upstream package metadata for:

- `claude-agent-sdk`
- `openai-codex`
- `openai-codex-cli-bin`
- `google-antigravity`

When `--refresh-preview` is used, the runner removes freshness-cutoff
environment variables and executes `uv lock --dry-run --exclude-newer false -P
...`. The repository no longer declares the retired eight-day cooloff or
package-specific exemptions, so candidate research, applied locks, and ordinary
locked CI use one policy.

## Candidate API Inspection

The command treats `uv.lock` as the current baseline and PyPI package metadata
as an independent candidate source. If the locked SDK is
missing from the active `.venv` or the installed version differs, the agent can
inspect the locked baseline in a temporary isolated virtualenv instead of
trusting the missing or drifted environment.
The current constrained preview is evidence, not the candidate inventory. When
an upstream release is excluded by an upper bound, the runner copies the
manifest and lock into a temporary workspace, widens only that excluding bound,
and performs a prospective no-cooloff preview. The original checkout is not
mutated during research. The Codex CLI candidate is the exact runtime selected
by the latest published Codex SDK. A newer standalone CLI wheel is recorded as
`sdk-coupled-no-update`: it is a release-staging artifact, not a blocked or
SDK-usable candidate. It may be inspected in a disposable environment for
implementation-history evidence, but it is never selected into the project
unless a published Codex SDK requires it.

With `--inspect-candidates`, the agent installs each missing or drifted locked
baseline, every exact compatible candidate, and the three most recent published
releases in credential-scrubbed temporary virtualenvs (throwaway `HOME`, `PATH`
only). One package/version environment is reused wherever API, behavior, and
implementation-history inspection overlap, and transient metadata/install
failures are retried. Candidate inspection writes the exact compatibility
transition to `api_diffs.json` and executes the adapter contract against that
same version. Independently, recent-release inspection fingerprints shipped
files and Python AST definitions, compares adjacent releases, and writes the
result to `implementation_diffs.json`. History also includes the exact locked
baseline and candidate when they fall outside the latest three releases, so
the changes between the installed baseline and the recent releases remain
visible. Claude probes construct permission, budget, session, MCP and schema
options; Codex probes construct configuration and validated thread/turn wire
parameters, including permissions, reasoning effort and structured output.
These checks complement real-adapter regression tests and live runtime checks;
they do not establish behavior inside opaque executables.
Source is never copied into the report;
only paths, counts, sizes, and hashes are retained. Candidate inspection is
opt-in because it executes freshly downloaded upstream code; historical
inspection uses the same explicit consent. Without the flag, missing trend
evidence is reported as `no-transition` rather than being replaced with
dependency advice.

## Upstream Implementation Trends

`direction_analysis.json` is an implementation-trend report. It answers what
changed across actual recent SDK implementations: modules and files, Python
definitions, source size, runtime artifacts, public API, observed adapter
behavior, capabilities, and deprecations. It does not recommend upgrading,
holding, resolving, or changing the lockfile; those decisions belong to
`architecture_decision.json` and the deterministic implementation gates.

The direction stage receives `implementation_diffs.json` as its primary
evidence. A deterministic postcondition replaces any release-operation advice
that leaks into an implementation-trend field. Opaque executables are reported
as opaque: artifact changes can be proved by hashes, but their internal design
cannot be inferred from a wheel.

If the independent candidate inventory contains an SDK update but the run cannot
produce an exact candidate-version API diff for that package, implementation is blocked and the
architecture decision is marked `manual_design_required`. An empty added /
removed / changed diff is valid; a missing diff object is not.

Behavior probes intentionally separate observed SDK surface churn from adapter
contract breakage. `behavior_probes.json` records fields and parameters and
constructs provider configurations with adapter-owned values; this catches
validation changes that field-presence inspection misses. `behavior_diffs.json`
compares the required adapter contract. `behavior_summary.json` records the deterministic
assessment, counts, and reasons used by the implementation gate. Its status is
`pass` for complete unchanged evidence, `changed` for complete non-breaking
changes, `incomplete` for probe errors, skips, malformed records, or missing
exact-version comparisons, and `fail` for a failed required contract or a
breaking diff. A package with no compatible candidate update needs only a valid
current baseline at the locked version (or the observed installed version when
no lock entry exists); it does not need a candidate probe. An ambient SDK that
has drifted away from the lock therefore produces `incomplete`, not `pass`.
Optional field changes remain visible in the report and API diffs without being
treated as a contract failure.

Candidate transitions are built once as exact `(package, from, to)` triples from
the independent package inventory plus successful prospective resolution, then
shared by snapshot collection, behavior assessment, and implementation gates.
An API diff or behavior comparison for a different package or version does not
satisfy the expected transition. Snapshot import and execution errors are also
shown explicitly in `report.md` instead of being hidden behind the snapshot
count. Behavior expectations come from deterministic package and resolver
evidence; a behavior payload cannot narrow that scope itself. The report and
`behavior_summary.json` recompute their assessment from raw probes and diffs, so
a stale or contradictory cached summary is never presented as the run result.

## Implementation Gates

Report-only mode is the default. To allow the implementation stage, pass:

```bash
uv run --locked --extra claude python -m examples.sdk_evolution_agent \
  --runtime claude-agent-sdk \
  --refresh-preview \
  --inspect-candidates \
  --implementation-enabled
```

Implementation is still blocked when:

- the architecture decision sets `manual_design_required`,
- the reviewer rejects the evidence or design,
- the no-cooloff prospective resolver preview is missing or failed,
- an independently discovered compatible update lacks an exact candidate API diff,
- required release-note evidence could not be collected,
- `behavior_summary.json` is missing, malformed, has an unknown status, reports
  `fail` / `incomplete`, is internally inconsistent, or uses the wrong
  package/version transition,
- required structured output or permission behavior is unsupported by the
  selected runtime,
- recursive self-adaptation is required but no safe migration plan exists.

Recursive self-adaptation means the agent noticed that an agent-runtime-kit
runtime-layer change affects its own use of `AgentTask`, `AgentResult`,
`RuntimeRegistry`, adapters, output schemas, event sinks, permission profiles,
or typed unsupported-feature errors. In that case, the agent must update its own
usage, schemas, tests, and docs in the same scoped change, or stop for manual
design review.

## Draft PRs

Draft PR creation is opt-in:

```bash
uv run --locked --extra claude python -m examples.sdk_evolution_agent \
  --runtime claude-agent-sdk \
  --refresh-preview \
  --inspect-candidates \
  --implementation-enabled \
  --create-branch \
  --branch-name sdk-evolution-update \
  --pr-base main \
  --draft-pr
```

When `--draft-pr` is set, publication occurs only after an applied, verified,
non-empty update. The agent stages only the `changed_paths` reported by the
implementation (normally `pyproject.toml`, `uv.lock`, and
`src/agent_runtime_kit/compatibility.py`), commits them with `--commit-message`,
pushes the branch, and opens a draft PR with `gh`. The gitignored report is
embedded in the PR body rather than blindly staged. A requested PR is skipped
when the update was blocked, empty, rolled back, or failed verification. It
never auto-merges.

The command uses local Git and `gh` authentication. It never auto-merges,
auto-publishes, or scrapes unsupported credentials.
