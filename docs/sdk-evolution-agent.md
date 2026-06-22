# SDK Evolution Agent

The SDK evolution agent is a local dogfood workflow for keeping
agent-runtime-kit aligned with Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK as those upstream packages evolve.

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
- `api_snapshots/`
- `api_diffs.json`
- `direction_analysis.json`
- `architecture_decision.json`
- `implementation_summary.json`
- `review.json`
- `events.jsonl`
- `report.md`

The report separates deterministic facts from runtime-generated analysis and
calls out uncertainty, recursive self-adaptation impact, implementation status,
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

By default, the command snapshots SDK APIs importable in the current
environment. Use `--inspect-candidates` to install latest candidate SDK versions
in temporary isolated virtualenvs for API snapshots and diffs. This avoids
mutating the project lockfile or working environment.

## Implementation Gates

Report-only mode is the default. To allow the implementation stage, pass:

```bash
python -m examples.sdk_evolution_agent --runtime claude-agent-sdk --implementation-enabled
```

Implementation is still blocked when:

- the architecture decision sets `manual_design_required`,
- the reviewer rejects the evidence or design,
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
  --draft-pr
```

The command uses local Git and `gh` authentication. It never auto-merges,
auto-publishes, or scrapes unsupported credentials.
