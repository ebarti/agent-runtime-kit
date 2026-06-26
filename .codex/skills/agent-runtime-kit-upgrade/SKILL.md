---
name: agent-runtime-kit-upgrade
description: Run the local agent-runtime-kit SDK evolution workflow to inspect and safely upgrade Claude Agent SDK, OpenAI Codex SDK, Codex CLI binary, and Google Antigravity SDK dependencies. Use when the user asks to upgrade, refresh, evolve, or create a PR for agent-runtime-kit against current upstream agent SDK releases.
---

# Agent Runtime Kit Upgrade

Run the local SDK evolution agent for `agent-runtime-kit`.

Default runtime for this Codex skill: `codex-agent-sdk`.

## Non-Negotiables

- Route all AI reasoning, planning, implementation decisions, structured output,
  and review through `python -m examples.sdk_evolution_agent`.
- Do not call OpenAI, Anthropic, Google, Bedrock, Vertex, or other model APIs
  directly from this skill.
- Use local tools only for deterministic work: Git, `gh`, `uv`, Python, package
  metadata fetching, filesystem inspection, SDK introspection, tests, and report
  inspection.
- Do not scrape unsupported credentials.
- Do not auto-merge or publish releases.
- Do not run from a dirty or divergent checkout. Create a fresh worktree from
  `origin/main` unless the user explicitly gives another base.

## Preflight

1. Announce the active checkout and the new worktree path.
2. Fetch remote state and create a fresh worktree:

   ```bash
   git fetch origin --prune
   git worktree add -b "sdk-evolution-upgrade-$(date +%Y%m%d-%H%M%S)" \
     /tmp/ark-sdk-evolution-upgrade origin/main
   ```

3. In the new worktree, verify local tooling:

   ```bash
   gh auth status
   env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
     uv run python -m examples.sdk_evolution_agent --help
   ```

4. Resolve the runtime that will run the AI-backed stages. Use
   `codex-agent-sdk` unless the user explicitly selected another runtime.

5. Use supported provider auth only:
   - Claude: Anthropic API key, Claude Code auth, or Claude Code provider
     settings such as Bedrock or Vertex. Bedrock must use the AWS SDK credential
     chain; Vertex must use Google Application Default Credentials or supported
     environment variables.
   - Codex: supported Codex login/API-key/access-token config. The runner
     injects `CODEX_HOME=~/.codex_agent_runtime_sdk` for Codex-backed stages, so
     authenticate that home before live Codex-backed runs.
   - Antigravity: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, or Google Application
     Default Credentials with a project and optional location variables.

6. If the selected runtime is `codex-agent-sdk`, fail fast unless the dedicated
   SDK Codex home is authenticated:

   ```bash
   env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
     CODEX_HOME="$HOME/.codex_agent_runtime_sdk" \
     uv run --extra codex codex login status
   ```

   If this prints `Not logged in` or exits non-zero, STOP before running
   `examples.sdk_evolution_agent`. Ask the user to authenticate the dedicated
   SDK home through one supported path:

   ```bash
   env CODEX_HOME="$HOME/.codex_agent_runtime_sdk" \
     uv run --extra codex codex login --device-auth
   ```

   API-key and access-token paths are also supported:

   ```bash
   printenv OPENAI_API_KEY | env CODEX_HOME="$HOME/.codex_agent_runtime_sdk" \
     uv run --extra codex codex login --with-api-key

   printenv CODEX_ACCESS_TOKEN | env CODEX_HOME="$HOME/.codex_agent_runtime_sdk" \
     uv run --extra codex codex login --with-access-token
   ```

## Report-Only First

Always run a report-only pass before implementation. Explicitly bypass uv
freshness cutoffs:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

If the user explicitly chooses another runtime, replace only the `--runtime`
value. Codex-backed runs should use the runner's built-in `gpt-5.5` and
`reasoning_effort=xhigh` policy; do not implement model selection outside the
runner.

Inspect the newest `reports/sdk-evolution/<timestamp>/` directory:

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

Stop before implementation when candidate API diffs are missing, required
release-note evidence is missing, behavior probes show breaking adapter-contract
drift, `manual_design_required` is true, the reviewer rejects the evidence or
design, or recursive self-adaptation lacks a safe migration plan.

Recursive self-adaptation means the upgrade affects the runner's own use of
`AgentTask`, `RuntimeRegistry`, adapters, output schemas, event sinks,
permission profiles, typed unsupported-feature errors, or `AgentResult`. The
upgrade must update those usages, tests, and docs in the same scoped change, or
stop for manual design review.

## Implementation And PR

Only run implementation when the report-only pass supports it and the user wants
an upgrade branch or PR:

```bash
BRANCH="sdk-evolution-upgrade-$(date +%Y%m%d-%H%M%S)"

env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --implementation-enabled \
    --create-branch \
    --branch-name "$BRANCH" \
    --draft-pr \
    --pr-base main \
    --commit-message "Run SDK evolution update" \
    --pr-title "Run SDK evolution update across vendor packages" \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

## Verification

Run or verify:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv lock --check
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run ruff check .
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run mypy
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run pytest
```

If a draft PR was created, watch CI until it finishes or clearly report that it
is still running. Final output should include the PR URL, report path, changed
SDK versions, architecture decision, reviewer result, test results, uncertainty,
and manual review checklist.
