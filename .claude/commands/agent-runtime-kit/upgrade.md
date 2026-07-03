---
name: "Agent Runtime Kit: Upgrade"
description: Run the local agent-runtime-kit SDK evolution workflow and produce a safe upgrade PR
category: Workflow
tags: [agent-runtime-kit, sdk-evolution, upgrade, workflow]
---

Run the local SDK evolution agent for `agent-runtime-kit`.

Use this when the user asks to upgrade, refresh, or evolve agent-runtime-kit
against current Claude Agent SDK, OpenAI Codex SDK, Codex CLI binary, or Google
Antigravity SDK releases.

Default runtime for this Claude command: `claude-agent-sdk`.

## Guardrails

- All AI reasoning, planning, implementation decisions, structured output, and
  review MUST go through `python -m examples.sdk_evolution_agent` and therefore
  through agent-runtime-kit runtime primitives.
- Do not call Anthropic, OpenAI, Google, Bedrock, Vertex, or other model APIs
  directly from this command.
- Local tools are allowed for deterministic work: Git, `gh`, `uv`, Python,
  package metadata fetching, filesystem inspection, SDK introspection, tests,
  and report inspection.
- Do not scrape unsupported credentials. Use only supported SDK auth surfaces.
- Do not auto-merge. Do not publish a release. Open or update a draft PR only
  when requested or when the user explicitly asks for an upgrade PR.
- Do not run from a dirty or divergent checkout. Create a fresh worktree from
  `origin/main` unless the user explicitly gives a different base.

## Inputs

The user may specify:

- runtime: `claude-agent-sdk`, `codex-agent-sdk`, or `antigravity-agent-sdk`
- package subset, otherwise inspect all supported packages
- branch name, otherwise derive one from the current timestamp
- whether to create a draft PR
- whether implementation is allowed

If unspecified, inspect all packages:

- `claude-agent-sdk`
- `openai-codex`
- `openai-codex-cli-bin`
- `google-antigravity`

## Preflight

1. Announce the active checkout and the new worktree path.
2. Fetch current remote state:

   ```bash
   git fetch origin --prune
   ```

3. Create a new worktree from the chosen base:

   ```bash
   git worktree add -b "sdk-evolution-upgrade-$(date +%Y%m%d-%H%M%S)" \
     /tmp/ark-sdk-evolution-upgrade origin/main
   ```

4. In the new worktree, verify local tooling:

   ```bash
   gh auth status
   env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
     uv run python -m examples.sdk_evolution_agent --help
   ```

5. Resolve the runtime that will run the AI-backed stages. Use
   `claude-agent-sdk` unless the user explicitly selected another runtime.

6. Verify provider auth through supported mechanisms only:
   - Claude: Anthropic API key, Claude Code auth, or Claude Code provider
     environment/settings such as Bedrock or Vertex modes. Use the AWS SDK
     credential chain for Bedrock and Google Application Default Credentials for
     Vertex; do not read credential files yourself.
   - Codex: local Codex auth/config. The SDK evolution runner injects
     `CODEX_HOME=~/.codex_agent_runtime_sdk` for Codex-backed stages and mirrors
     the normal `~/.codex/auth.json` cache into that isolated home before use.
   - Antigravity: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or Google Application
     Default Credentials with project/location environment variables.

7. If the selected runtime is `codex-agent-sdk`, prepare and verify fresh auth
   for the dedicated SDK Codex home before running the evolution agent:

   ```bash
   env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
     uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
   ```

   The helper creates `~/.codex_agent_runtime_sdk`, removes uv freshness cutoff
   variables, mirrors `~/.codex/auth.json` into that isolated home when the
   normal cache is newer, and checks `codex login status` against that exact
   home. If it exits non-zero, STOP before running `examples.sdk_evolution_agent`
   and refresh the normal Codex login cache:

   ```bash
   uv run --extra codex codex login --device-auth
   env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
     uv run --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
   ```

## Report-Only Evidence Pass

Run a report-only pass first. Explicitly bypass freshness cutoffs because fresh
upstream SDK releases are the point of this workflow:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python -m examples.sdk_evolution_agent \
    --runtime claude-agent-sdk \
    --mode report \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

If the user chose another runtime, replace only the `--runtime` value. Do not
add direct model calls.

Inspect the newest `reports/sdk-evolution/<timestamp>/` directory and summarize:

- `evidence.json`
- `release_notes.json`
- `api_diffs.json`
- `behavior_probes.json`
- `behavior_diffs.json`
- `current_state.json`
- `direction_analysis.json`
- `architecture_decision.json`
- `review.json`
- `report.md`

Stop before implementation if any of these are true:

- required candidate API diffs are missing,
- required release-note evidence could not be collected,
- `behavior_diffs.json` contains breaking adapter-contract drift,
- `architecture_decision.json` has `manual_design_required: true`,
- the reviewer rejects the evidence or design,
- recursive self-adaptation is required and the report does not include a safe
  migration plan for the agent's own use of `AgentTask`, `RuntimeRegistry`,
  adapters, output schemas, event sinks, permission profiles, or
  `AgentResult`.

## Implementation Pass

Only run implementation when the report-only pass supports it and the user wants
an upgrade branch or PR:

```bash
BRANCH="sdk-evolution-upgrade-$(date +%Y%m%d-%H%M%S)"

env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python -m examples.sdk_evolution_agent \
    --runtime claude-agent-sdk \
    --mode upgrade-pr \
    --branch-name "$BRANCH" \
    --pr-base main \
    --commit-message "Run SDK evolution update" \
    --pr-title "Run SDK evolution update across vendor packages" \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

If the user chose another runtime, replace only the `--runtime` value.

Successful upgrade PRs commit the mechanical update artifacts: `uv.lock`,
`pyproject.toml` only when a cap raise was applied, and the tracked
`.sdk-evolution/` baseline. The timestamped `reports/sdk-evolution/` directory
is local and gitignored; its evidence is embedded in the PR body.

## Verification

After implementation, run or verify:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv lock --check
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run ruff check .
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run mypy
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run pytest
```

If a draft PR was created, watch CI until it finishes or clearly report that it
is still running. Include the PR URL, report path, changed SDK versions,
architecture decision, reviewer result, test results, uncertainty, and manual
review checklist in the final response.
