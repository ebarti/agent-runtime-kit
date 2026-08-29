---
name: agent-runtime-kit-upgrade
description: Inspect and safely upgrade agent-runtime-kit's Claude Agent SDK, OpenAI Codex SDK and coupled CLI binary, and Google Antigravity SDK dependencies. Use for SDK freshness checks, upgrades, compatibility repairs, or upgrade PRs in agent-runtime-kit.
---

# Agent Runtime Kit SDK Upgrade

Use the repository-owned evolution runner for evidence collection, candidate
inspection, compatibility gates, dependency updates, and verification.

## Authorization

- Always run a report-only pass before changing dependencies.
- An explicit request to upgrade, refresh, or update the SDKs authorizes the
  gated local `pyproject.toml`, `uv.lock`, and compatibility-manifest update
  after the report passes.
- A bare skill invocation or freshness question is report-only.
- Creating a branch, commit, push, or pull request requires an explicit request
  for that publication action. Never auto-merge or publish a release.

## Invariants

- Discover each monitored package's latest upstream release independently of
  the repository's current constraints. A constraint that excludes a newer
  release is a result to investigate, not proof that the repository is current.
- Keep these facts separate in the report: upstream latest, compatible
  candidate, current resolver result, prospective resolver result, and applied
  lock version.
- `openai-codex-cli-bin` is coupled to the exact version required by the
  latest published `openai-codex`. Record a newer standalone CLI release as a
  staged artifact, not a blocked candidate. It may be fingerprinted in a
  credential-scrubbed disposable environment for implementation-history
  analysis, but never select it into the project unless the SDK requires it.
- Direction-of-travel means trends in actual upstream implementations. Inspect
  recent release files and Python definitions even when no dependency update is
  pending. Never turn the direction report into upgrade, hold, resolver, or
  lockfile advice; that belongs to the architecture and implementation stages.
- Run UV without freshness delays. The runner removes cutoff environment
  variables and uses `uv lock --exclude-newer false`; do not reintroduce the
  retired repository cooloff or package-specific exemptions.
- Candidate API snapshots and behavior probes must cover every exact
  `(package, baseline, candidate)` transition. Field presence alone is not a
  semantic probe: construct the provider configuration and exercise the
  adapter-owned contract.
- Never promote or create a PR unless the update produced a non-empty diff, the
  lock contains every inspected compatible candidate exactly, and all
  verification commands passed.

## Checkout Preflight

Fetch and compare the current checkout with the requested base. Work directly
only when it is the intended clean checkout. Preserve dirty or unrelated work.
When isolation is needed, use a unique path rather than the old shared `/tmp`
path:

```bash
git fetch origin --prune
scratch="$(mktemp -d "${TMPDIR:-/tmp}/ark-sdk-evolution.XXXXXX")"
worktree="$scratch/worktree"
branch="sdk-evolution-upgrade-$(date +%Y%m%d-%H%M%S)-$$"
git worktree add -b "$branch" "$worktree" origin/main
```

Announce the selected checkout and base. Do not delete a worktree that contains
uncommitted work. If repository instructions mention a GSD command that is not
actually installed, record the investigation and plan in the available task
plan, then continue; an unavailable wrapper is not a reason to abandon an
explicitly requested repair.

## Runtime Preflight

Use `codex-agent-sdk` unless the user selected another runtime. Match runtime
and optional extra:

- `claude-agent-sdk` -> `--extra claude`
- `codex-agent-sdk` -> `--extra codex`
- `antigravity-agent-sdk` -> `--extra antigravity`

Use supported authentication only. For Codex, prepare the dedicated SDK home:

```bash
env -u UV_EXCLUDE_NEWER \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent.auth ensure-codex
```

If authentication fails, stop and report that concrete blocker. Do not scrape
or reconstruct credentials.

## Report-Only Pass

Run all four monitored packages unless the user explicitly narrowed scope:

```bash
env -u UV_EXCLUDE_NEWER \
  uv run --locked --extra codex python -m examples.sdk_evolution_agent \
    --runtime codex-agent-sdk \
    --refresh-preview \
    --inspect-candidates \
    --package claude-agent-sdk \
    --package openai-codex \
    --package openai-codex-cli-bin \
    --package google-antigravity
```

`--inspect-candidates` is explicit consent to install and import exact locked baselines and candidate
versions in credential-scrubbed temporary environments. The runner reuses each
package/version environment for its API snapshot and behavior probe, retries
transient metadata/install failures, and prints progress while it works.

Read the newest `reports/sdk-evolution/<timestamp>/report.md` plus its raw JSON
artifacts, especially `implementation_diffs.json` and `behavior_summary.json`.
The implementation-trend section must describe observed source/artifact changes
and explicit binary limitations, never dependency action. Treat any of these as a hard
implementation blocker:

- missing or failed no-cooloff prospective resolver preview;
- upstream candidate absent from the exact API-diff inventory;
- missing required release evidence;
- behavior status `fail` or `incomplete`, malformed/contradictory evidence, or
  a missing exact-version comparison;
- `manual_design_required`, reviewer rejection, or an unsafe architecture
  decision;
- recursive runtime impact without an explicit adaptation and rerun plan.

`pass` means complete unchanged behavior evidence. `changed` means complete,
non-breaking behavior evidence. Neither status permits ignoring snapshot import
errors or a failed resolver preview.

## Apply a Requested Local Upgrade

When the user explicitly requested an upgrade and the report-only pass is green,
rerun with `--implementation-enabled` and without PR flags:

```bash
env -u UV_EXCLUDE_NEWER \
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

The deterministic implementation lane widens only excluding upper bounds,
updates the lock with `--exclude-newer false`, verifies exact target versions,
updates `src/agent_runtime_kit/compatibility.py` to the exact tested SDK and
coupled-runtime versions, and rolls all three artifacts back on resolver
mismatch or test failure. If a candidate requires adapter source changes, the
runner must block; repair the evidenced contract in the scoped coding workflow,
add a regression test, and rerun the report before applying dependencies.

## Pull Request Mode

Only when the user requested a PR, add a unique branch and the PR flags to the
green implementation pass:

```bash
--create-branch --branch-name "$branch" --draft-pr --pr-base main
```

The runner may stage only its reported `changed_paths`. Verify the exact pushed
head, PR URL, changed files, and CI state. Do not claim a PR exists merely
because `--draft-pr` was requested; the runner skips publication when the
change is empty, unapplied, or unverified.

## Final Verification

Verify the deciding live surface again:

```bash
env -u UV_EXCLUDE_NEWER uv lock --check
env -u UV_EXCLUDE_NEWER uv run --locked ruff check .
env -u UV_EXCLUDE_NEWER uv run --locked mypy
env -u UV_EXCLUDE_NEWER uv run --locked pytest
```

Report the baseline and applied versions, candidate classifications, behavior
status, exact verification results, changed files, report path, and any
remaining blocker or uncertainty. For PR work, also report the verified URL,
head SHA, and current checks.
