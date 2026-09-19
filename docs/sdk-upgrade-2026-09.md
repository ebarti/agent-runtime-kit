# September 2026 SDK compatibility review

The September 19 review starts at `22bbd26` and adopts Claude `0.2.157`,
Codex SDK and CLI `0.154.0`, and Antigravity `0.1.17`. Codex's upper bound is
widened only to the next minor; the SDK selects its exact CLI dependency.
Optional extras and Python 3.10 support remain part of the verification contract.

## Upstream impact decisions

| Evidence and change | Adapter impact and decision |
| --- | --- |
| Claude `0.2.152` to `0.2.157`: `SystemPromptCustom` and snapshot controls, introduced in `0.2.153`; subsequent bundled CLI updates. | Existing string `AgentTask.system`, permission, MCP, session and output-format options still construct successfully. Preserve the string contract and vendor prompt-snapshot defaults. Snapshot tuning is a session-level vendor option outside the current string-only task system field; callers must not assume a new system string rebuilds a resumed Claude session's recorded prompt. No new shared prompt-policy type is introduced. |
| Codex `0.147.0` to `0.154.0`: independent turn subscriptions and lifecycle bookkeeping. | Continue using `AsyncThread.run`, which owns its turn subscription and cleanup. Verify repeated runs and session resume with the final coupled runtime; retain adapter cancellation, process eviction and task isolation tests. |
| Codex adds `ExternalMessage`, `source`, `turn_service_tier`, and `include_turns`. | Preserve `AgentTask.goal` as caller instructions; do not silently recast it as lower-authority external content. Turn-source analytics and pricing policy remain with callers. The adapter consumes returned results and session IDs, so it does not need to request historical turns on resume. |
| Antigravity `0.1.17`: `resolve_active_tools` subtracts deny-lists from `BuiltinTools.default()`, which excludes `ASK_QUESTION`. | Adapt now: translate permissive deny-lists to an explicit all-tools-minus-denied allow-list. This preserves the existing adapter contract and subagent enablement while retaining stricter filesystem and permission baselines. A real-SDK regression demonstrates the original loss and the repair. |
| Antigravity adds compaction configuration, scoped token/call budgets, tool-output truncation and usage arithmetic. | No existing adapter path sets the deprecated compaction threshold or these optional tuning controls. Keep vendor defaults. Token/call budgets cannot faithfully implement dollar-denominated `budget_usd`, which remains explicitly unsupported. Usage translation still reads individual token fields, preserving missing values. |
| Antigravity exposes OS sandbox status and improves tool-argument conversion. | The adapter's permission mapping controls available tools and policies; it does not request or promise the new OS command sandbox. Tool audits consume the SDK's normalized arguments, so the conversion improvements remain supported without copying the provider's parser. |

Primary sources: [Claude SDK changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md),
[Codex SDK](https://learn.chatgpt.com/docs/codex-sdk),
[Codex changelog](https://learn.chatgpt.com/docs/changelog), and
[Antigravity 0.1.17 notes](https://github.com/google-antigravity/antigravity-sdk-python/discussions/210).
The exact wheel APIs, Python definitions and artifact fingerprints are collected
by the evolution runner. Native binary fingerprints establish artifact changes,
not the binaries' internal behavior. Claude's published changelog currently ends
at `0.2.156`, so exact `0.2.157` release-note coverage remains an explicit limit.

## Evidence workflow adaptations

- Keep the locked baseline in implementation history even when newer releases
  have pushed it outside the three-release window. This prevents the Claude
  snapshot-control change from disappearing from direction analysis.
- Construct real Claude option values and Codex configuration/thread/turn wire
  values in both ambient and isolated probes. Field presence alone is insufficient.
- Use current official Codex documentation endpoints; the previous URLs return
  HTTP 308 redirects that the Python 3.10 collector cannot follow.
- Select AI-stage model and reasoning effort through explicit runner options.
  A `--codex-bin` override is analysis-driver provenance, separate from candidate
  compatibility. Post-update verification must omit it and use the bundled CLI.

The initial report passed the existing gates but did not catch the Antigravity
tool-set regression. The added real-adapter regression and cumulative source
review therefore remain necessary alongside the runner's automated evidence.

## Verification

The final locked environment on Python 3.10 passes 553 tests with 90.36% package
coverage, Ruff, strict mypy and the lock consistency check. The minimum direct
dependency environment passes 552 tests. The three opt-in live-provider tests
are skipped in both runs; the floor run also skips endpoint-model inspection
that requires Antigravity 0.1.15 or newer. Antigravity 0.1.17 emits five
upstream deprecation warnings while validating capabilities.

A separate live check using the locked Codex SDK and its bundled CLI, both
`0.154.0`, passes structured output, independent task sessions, explicit session
resume, process reuse and cleanup with `gpt-6-astra` and `xhigh`. This check uses
no executable override. Claude and Antigravity validation is limited to local
configuration, adapter and regression tests; their live services were not run.
