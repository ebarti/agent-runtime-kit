# SDK Evolution Agent

The SDK evolution agent is a local dogfood workflow for keeping
agent-runtime-kit aligned with Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK as those upstream packages evolve.

For the intended architecture, evidence contract, behavior probe strategy,
changelog strategy, caveats, and alternatives, see
[`docs/sdk-evolution-agent-design.md`](sdk-evolution-agent-design.md).

Run it from the repository:

```bash
python -m examples.sdk_evolution_agent --runtime fake
```

The `fake` runtime is deterministic and useful for checking the local pipeline
without credentials. For real AI reasoning, select a configured runtime:

```bash
python -m examples.sdk_evolution_agent --runtime claude-agent-sdk
python -m examples.sdk_evolution_agent --runtime codex-agent-sdk
python -m examples.sdk_evolution_agent --runtime antigravity-agent-sdk
```

Every AI-backed stage is dispatched as an `AgentTask` through a runtime resolved
from `RuntimeRegistry`. The agent does not call OpenAI, Anthropic, Google, or
other model APIs directly for reasoning, planning, implementation, review, or
structured output.

When `--runtime codex-agent-sdk` is selected, the agent injects
`CODEX_HOME=~/.codex_agent_runtime_sdk` into the Codex SDK subprocess. This keeps
the dogfooded SDK evolution agent's Codex state separate from a user's normal
Codex home while still using supported Codex authentication mechanisms. The
directory is created with private permissions before the Codex runtime starts;
authenticate that Codex home through supported Codex login/API-key/access-token
flows before using it for real Codex-backed runs.

Codex-backed SDK evolution runs explicitly choose `gpt-5.5` with
`reasoning_effort=xhigh` for the AI stages that analyze direction, decide the
update plan, implement allowed changes, and review the result. This model policy
is applied only to `codex-agent-sdk`; Claude and Antigravity runs keep their
provider-native model selection because `gpt-5.5` is not a valid model override
for those adapters.

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

When `--refresh-preview` is used, the targeted `uv lock --dry-run -P ...`
preview runs with freshness cutoff environment variables removed, including
`UV_EXCLUDE_NEWER`. This workflow needs fresh upstream SDK information, so local
cutoff variables must not hide candidate releases.

## Candidate API Inspection

The command treats `uv.lock` as the current baseline. If the active `.venv`
contains a different installed version, the agent inspects the locked baseline
in a temporary isolated virtualenv instead of trusting the drifted environment.
When a refresh preview is available, package update candidates come from the
resolver's `uv lock --dry-run -P ...` output, not only from PyPI's `latest`
metadata. For each resolver update candidate, the agent installs the target
version in a temporary isolated virtualenv and writes an API snapshot plus
`api_diffs.json` entry. This avoids false downgrade diffs for packages whose
locked prerelease is newer than PyPI's stable latest field. Candidate inspection
is always enabled for update candidates; `--inspect-candidates` remains accepted
only for CLI compatibility.

If `uv lock --dry-run -P ...` reports an SDK update but the run cannot produce a
candidate-version API diff for that package, implementation is blocked and the
architecture decision is marked `manual_design_required`. An empty added /
removed / changed diff is valid; a missing diff object is not.

## Implementation Gates

Report-only mode is the default. To allow the implementation stage, pass:

```bash
python -m examples.sdk_evolution_agent --runtime claude-agent-sdk --implementation-enabled
```

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
python -m examples.sdk_evolution_agent \
  --runtime claude-agent-sdk \
  --implementation-enabled \
  --create-branch \
  --branch-name sdk-evolution-update \
  --pr-base main \
  --draft-pr
```

When `--draft-pr` is set, the agent stages `uv.lock` and the run report
directory, commits them with `--commit-message`, pushes the branch, and opens a
draft PR with `gh`. It never auto-merges.

The command uses local Git and `gh` authentication. It never auto-merges,
auto-publishes, or scrapes unsupported credentials.
