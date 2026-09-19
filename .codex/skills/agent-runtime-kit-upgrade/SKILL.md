---
name: agent-runtime-kit-upgrade
description: Upgrade agent-runtime-kit's Claude, Codex and coupled CLI, and Antigravity SDK integrations, implement the adaptations required by upstream evolution, verify the complete change, and create a pull request. Also supports explicitly requested report-only freshness and compatibility checks.
---

# Agent Runtime Kit SDK Upgrade

Complete the SDK upgrade through a verified pull request. Use the repository's
evolution runner for evidence collection, direction analysis, architecture and
review gates, and atomic dependency updates. Implement the necessary runtime,
adapter, public API, test, and documentation changes as part of the same work.

## Scope and Completion

- An explicit invocation of this upgrade skill, or a request to upgrade, update,
  or refresh these SDK integrations, requests the complete workflow: investigate,
  adapt, update dependencies, verify, create a branch, commit, push, and open a
  regular pull request. Do not stop after producing a report or ask for the same
  authorization again at each stage.
- Honor narrower user instructions. A freshness/status question or an explicit
  report-only request performs inspection without changes or publication. A
  local-only upgrade performs the adaptation and verification without a push
  or PR. Never infer upgrade authority merely from a question about versions.
- Always collect and review evidence before implementation. Report-first is a
  sequencing requirement; the requested upgrade is complete when the necessary
  adaptations and dependency changes are in a verified PR.
- Never auto-merge or publish a release. If there is no justified change, report
  the evidence and do not manufacture a diff or an empty PR.

## Checkout and Runtime Preflight

Fetch `origin` and compare the checkout with the requested base, normally
`origin/main`. Preserve unrelated dirty work. Use an intended clean checkout or
a fresh worktree with a unique path. For an upgrade, create one unique branch;
for report-only isolation, use a detached worktree. Keep using that worktree
when later reruns contain this workflow's own reviewed edits.

Announce the selected checkout and base. If repository instructions mention a
GSD command that is not installed, record the plan with an available planning
mechanism and continue the authorized work; do not wait for a missing wrapper.

Use `codex-agent-sdk` unless the user selected another runtime. Match the extra:

- `claude-agent-sdk` -> `--extra claude`
- `codex-agent-sdk` -> `--extra codex`
- `antigravity-agent-sdk` -> `--extra antigravity`

Use supported authentication only. For Codex, prepare the dedicated SDK home:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

If authentication fails, report the concrete blocker. Do not scrape or
reconstruct credentials. Honor an explicitly requested model and reasoning
effort rather than silently using the runner's defaults. Pass explicit runner
options such as `--model gpt-6-astra --reasoning-effort xhigh`; `config.json`
records them, and each AI stage uses the corresponding first-class task fields.
Verify that selection with a small structured runtime call before expensive
collection. The installed
SDK's supported `CodexConfig.codex_bin` and the adapter's `config_cls` injection
can select an already-installed executable when the bundled CLI cannot run the
requested model. Record that analysis-driver override separately from the
inspected and locked SDK/CLI versions; it does not establish package compatibility.
The runner exposes this supported configuration as `--codex-bin /absolute/path/to/codex`.
Omit that override for the post-update verification through the final bundled CLI.

## Evidence Pass

Run all four monitored packages unless the user narrowed scope:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --inspect-candidates \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

`--inspect-candidates` is explicit consent to install and import exact locked
baselines and candidates in credential-scrubbed temporary environments. The
runner reuses each package/version environment for API snapshots and behavior
probes, retries transient collection failures, and prints progress.

Read `reports/sdk-evolution/<timestamp>/report.md` and the raw artifacts,
especially `evidence.json`, `api_diffs.json`, `implementation_diffs.json`,
`release_notes.json`, `behavior_probes.json`, `behavior_diffs.json`,
`behavior_summary.json`, `direction_analysis.json`, `architecture_decision.json`,
and `review.json`.

Preserve these evidence invariants:

- Discover latest upstream releases independently of current constraints. Keep
  upstream latest, compatible candidate, current resolver, prospective resolver,
  and applied lock versions separate. An excluding bound is an investigation
  finding, not proof that the integration is current.
- Run UV without freshness delays: remove cutoff variables and use
  `uv lock --exclude-newer false`. Do not reintroduce cooloffs or exemptions.
- The Codex CLI candidate is the exact dependency required by the published
  Codex SDK. A newer standalone CLI is a staged artifact for implementation
  history; never select it into the package unless the SDK requires it.
- Inspect recent upstream release files and Python definitions even when no
  version bump is pending. Describe observed implementation trends and binary
  visibility limits in the direction report; put adaptation decisions in the
  architecture plan.
- Cover every exact `(package, baseline, candidate)` transition with API and
  behavior evidence. Construct real provider configurations using values accepted
  by the public API and exercise the adapter-owned contract. Field presence alone
  does not establish semantic compatibility.

## Adapt to Upstream Evolution

For every substantive implementation trend, trace its impact through the current
runtime and adapter. Record the evidence, the existing behavior, and the decision:
adapt now, already supported, or outside this package's scope with a concrete
reason. Consider changed options and defaults, permissions, tools/MCP, sessions,
events, results, structured output, lifecycle, and capabilities where the evidence
shows an impact. Passing the current probes does not by itself prove that the
integration exposes the vendor capabilities it should support.

Implement the necessary changes in the owning layer, including regression
coverage for the observed contract and relevant docs. Preserve the small typed
runtime API, vendor capabilities, optional extras, and Python 3.10 support.
Avoid speculative features or importing upstream orchestration/model policy.
When the change affects the evolution runner's own use of `AgentTask`,
`AgentResult`, `RuntimeRegistry`, adapters, output schemas, event sinks,
permissions, or typed errors, adapt those usages and rerun the workflow too.

The runner's deterministic implementation lane updates dependencies and the
compatibility manifest; it does not implement adapter source changes. Perform
those changes in the repository coding workflow, then rerun the evidence and
review stages. Do not treat that runner limitation as a reason to abandon an
authorized adaptation or reduce the task to version bumps.

These findings block promotion until resolved: missing/failed resolver or
release evidence; an absent exact candidate API diff; snapshot import errors;
behavior status `fail` or `incomplete`; contradictory evidence;
`manual_design_required`; reviewer rejection; or unsafe recursive adaptation.
Investigate, repair the owned contract or evidence collector, and rerun the
gates. Do not override a rejection, weaken a probe, or relabel missing evidence
to obtain a pass. Ask for user input only for an unresolved product decision,
missing authorization, or external blocker that prevents further progress.

`pass` means complete unchanged behavior evidence. `changed` means complete,
non-breaking evidence. Neither permits ignoring other failed gates.

## Apply and Verify the Upgrade

Once the adaptation and evidence support promotion, run the implementation pass:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --inspect-candidates \
    --implementation-enabled \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

The runner widens only excluding upper bounds, resolves without cooloffs,
verifies exact targets, and updates `src/agent_runtime_kit/compatibility.py`.
Project declarations, `uv.lock`, and the compatibility manifest form one atomic
update and roll back together on mismatch or verification failure. Preserve
the adaptation edits and fix the evidenced failure before retrying.

Verify the complete diff, including source adaptations, against the final lock:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv lock --check
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked ruff check .
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked mypy
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE uv run --locked pytest
```

Run targeted contract/regression checks for the changed behavior. Distinguish
local semantic probes from live provider checks and disclose skips. Review the
actual cumulative source and dependency diff; a green pre-change design review
does not replace review of the implementation.

## Create the Pull Request

Complete this step for the default upgrade workflow without another permission
question. Stage only the reviewed SDK upgrade and adaptation paths, including
their tests and docs; the runner's dependency-only `changed_paths` is not the
whole scope when source changes were necessary. Keep reports and unrelated work
out of the commit.

Commit, push the unique branch, and use `gh pr create` to open a regular PR
against the requested base. Do not use the runner's `--draft-pr` flag. Write the
PR body to a temporary file and pass `--body-file`; describe the upstream changes,
resulting integration behavior, exact versions, and validation. Update an
existing PR for this branch instead of creating a duplicate.

Verify the pushed head, PR URL, base/head, changed files, and CI. Resolve relevant
failures and wait for checks to finish, or identify a concrete external blocker.
The handoff must include the PR URL, exact versions, implemented adaptations and
justified scope decisions, review/verification results, report paths, and any
remaining uncertainty. For an explicit report-only or local-only request, stop
at its requested boundary instead.
