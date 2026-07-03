# SDK Evolution Agent

The SDK evolution agent is a local dogfood workflow for keeping
agent-runtime-kit aligned with Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK as those upstream packages evolve.

For the intended architecture, evidence contract, behavior probe strategy,
changelog strategy, caveats, and alternatives, see
[`docs/sdk-evolution-agent-design.md`](sdk-evolution-agent-design.md).

Run it from the repository:

```bash
python -m examples.sdk_evolution_agent --mode report --runtime fake
```

The `fake` runtime is deterministic and useful for checking the local pipeline
without credentials. For real AI reasoning, select a configured runtime:

```bash
python -m examples.sdk_evolution_agent --mode report --runtime claude-agent-sdk
python -m examples.sdk_evolution_agent --mode report --runtime codex-agent-sdk
python -m examples.sdk_evolution_agent --mode report --runtime antigravity-agent-sdk
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
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

The helper checks `codex login status` against the dedicated home and can refresh
that home by copying the normal `~/.codex/auth.json` cache when it is newer. If
the helper reports that the isolated home is not authenticated, refresh the
normal Codex login cache and rerun the helper:

```bash
uv run --extra codex codex login --device-auth
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

Codex-backed SDK evolution runs explicitly choose `gpt-5.5` with
`reasoning_effort=xhigh` for the AI stages that analyze direction, decide the
update plan, implement allowed changes, and review the result. This model policy
is applied only to `codex-agent-sdk`; Claude and Antigravity runs keep their
provider-native model selection because `gpt-5.5` is not a valid model override
for those adapters.
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
- `behavior_probes.json`
- `behavior_diffs.json`
- `current_state.json`
- `direction_analysis.json`
- `architecture_decision.json`
- `implementation_summary.json`
- `review.json`
- `events.jsonl`
- `report.md`

The report separates deterministic facts from runtime-generated analysis and
calls out uncertainty, release-note coverage, API diffs, behavior diffs,
baseline promotion, recursive self-adaptation impact, implementation status,
test results, reviewer output, and manual review items.

## Upstream Freshness

The agent checks the current project state in `pyproject.toml`, `uv.lock`, and
installed distributions, then compares it with upstream package metadata for:

- `claude-agent-sdk`
- `openai-codex`
- `openai-codex-cli-bin`
- `google-antigravity`

`--mode report` enables the refresh path. The resolver copies `pyproject.toml`,
`uv.lock`, and the minimal README metadata into a temporary directory, runs
targeted `uv lock -P ...` there with freshness cutoff environment variables
removed, then diffs the temporary lockfile against the real one. The real
workspace is not touched during candidate detection. The older dry-run stdout
preview may still be stored as auxiliary evidence, but it is not parsed for
candidate decisions.

## Candidate API Inspection

The command treats `uv.lock` as the current baseline. If the active `.venv`
contains a different installed version, the agent inspects the locked baseline
in a temporary isolated virtualenv instead of trusting the drifted environment.
When refresh evidence is available, package update candidates come from the
temporary lockfile diff, not PyPI's `latest` metadata and not human-facing uv
stdout. With `--inspect-candidates`, the agent installs each resolver update
candidate and beyond-cap candidate in a temporary isolated virtualenv — with a
credential-scrubbed environment (throwaway `HOME`, `PATH` only) — then writes an
API snapshot plus `api_diffs.json` entry and runs behavior probes against the
candidate the same way. Candidate inspection is opt-in because it executes
freshly downloaded upstream code; without the flag, candidates are recorded as
explicit `skip` entries rather than silently missing evidence.

If the structured resolver reports an SDK update but the run cannot produce a
candidate-version API diff for that package, implementation is blocked and the
architecture decision is marked `manual_design_required`. An empty added /
removed / changed diff is valid; a missing diff object is not.

Behavior probes intentionally separate observed SDK surface churn from adapter
contract breakage. `behavior_probes.json` records fields and parameters seen in
current and candidate packages, while `behavior_diffs.json` compares the
required adapter contract. Optional field changes remain visible in the report
and API diffs, but only breaking adapter-contract diffs block implementation.

## Implementation Gates

Report-only mode is the default. To allow the implementation stage, pass:

```bash
python -m examples.sdk_evolution_agent --mode upgrade --runtime claude-agent-sdk
```

`--mode upgrade` expands to report + candidate inspection + implementation +
guarded cap raises. It refuses to run from a dirty worktree unless `--allow-dirty`
is set for local development.

Implementation is still blocked when:

- the architecture decision sets `manual_design_required`,
- the reviewer rejects the evidence or design,
- a resolver-selected update lacks a candidate API diff,
- required release-note evidence could not be collected,
- candidate behavior probes show a breaking adapter-contract difference,
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
python -m examples.sdk_evolution_agent --mode upgrade-pr --runtime claude-agent-sdk
```

`--mode upgrade-pr` creates a `sdk-evolution/<run_id>` branch when one is not
supplied, refuses to push the default branch, stages `uv.lock`,
`pyproject.toml` when a cap raise was applied, and the tracked `.sdk-evolution/`
baseline. The report directory remains local and gitignored; the draft PR body
embeds report evidence plus a machine-readable SDK-evolution marker. The PR
flow runs only after implementation is applied and verification passes.

The command uses local Git and `gh` authentication. It never auto-merges,
auto-publishes, or scrapes unsupported credentials.
