# October 2026 SDK integration review

The October 2 upgrade starts at `da3cf8a` and targets Claude Agent SDK
`0.2.163`, Codex SDK and its exactly coupled CLI `0.160.0`, and Antigravity
`0.1.20`. These are the latest stable PyPI releases observed that day. Keep
Python 3.10 support, provider extras, and the existing Codex `0.157.1` minimum
needed for named permission profiles; raise its excluding upper bound to
`<0.161`.

## Upstream changes and adapter decisions

| Observed upstream change | Impact and decision |
| --- | --- |
| Claude `0.2.157` to `0.2.163`: new `verbatim_prompts`, plus session-state handling and bounded background-subagent input lifetime. | Expose `ClaudeAgentRuntime(verbatim_prompts=True)` as an opt-in constructor option for composed goals. Require SDK support instead of silently dropping it. Preserve existing defaults because verbatim delivery also skips the initial attachment pass. Continue using SDK-owned `query()` and `ClaudeSDKClient` lifecycle management rather than duplicating the new run-end state machine. |
| Codex `0.157.1` to `0.160.0`: generated protocol adds history anchors, MCP server discovery filtering and error variants, and removes plugin UI models. | Already supported or outside scope: the adapter uses unchanged `AsyncThread.run`/thread APIs, preserves interrupted-turn error messages, and does not expose history pagination or plugin management. Keep native permission profiles, sandbox mapping, schemas, reuse and typed model/effort selection. Upgrade the SDK and its exact CLI dependency together. |
| Antigravity `0.1.20`: directory/search tools move from default collections to `deprecated()`; `schedule` joins the read-only/nondestructive collections. | Adapt explicit read-only allowlist validation to admit the three known legacy read-only tools that the SDK still supports. Keep SDK-owned default and deny-list collections; do not add legacy tools back to defaults or admit arbitrary deprecated write tools. Existing enum validation permits callers to explicitly select or deny `schedule`. |
| Antigravity policy auto-review, subagent model overrides and evaluation presets. | Outside this package's current task contract: permission modes already map to explicit capabilities and policies; the kit does not configure vendor subagents or benchmark orchestration. Do not switch callers to new policies or evaluation defaults implicitly. |
| Antigravity local event parsing tolerates unknown service tiers, handles policy denials and suppresses error-step text. | Already delegated to the SDK before the adapter consumes typed chunks. Continue normalizing token usage and stop reasons; do not duplicate the harness's parsing/policy machinery. |
| Bundled Claude CLI, Codex executables/resources and Antigravity harness change. | Artifact fingerprints establish distribution changes, not binary internals. Verify the final bundled Codex CLI through live structured tasks and keep live Claude/Antigravity execution distinct from local configuration probes. |

## Evidence and validation boundaries

The repository evolution runner collects exact baseline/candidate API and
configuration probes, recent-release Python definition/file diffs, upstream
release sources, resolver evidence, and direction/architecture/review gates.
All sixteen recent-release implementation snapshots and the four exact
baseline/candidate comparisons completed successfully before application.
Resolution, declarations, lockfile and compatibility manifest are updated
together by the runner's verified atomic transaction.

Regression coverage exercises both Claude dispatch paths, unsupported-SDK
refusal, and real SDK option construction. Antigravity tests exercise real SDK
explicit legacy tool allowlists, default preservation, and rejection of write
tools, including a hypothetical deprecated write tool. These configuration
checks do not contact providers or construct live Antigravity agents.

Official Antigravity sources did not provide a matching `0.1.20` release-note
entry during collection. Exact published wheel sources and executable
configuration probes establish the inspected changes; retain the release-note
coverage limit separately from compatibility findings.

Sources: [Claude SDK changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md),
[Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk),
[Codex releases](https://github.com/openai/codex/releases),
[Antigravity SDK](https://github.com/google-antigravity/antigravity-sdk-python),
and the published [Claude](https://pypi.org/project/claude-agent-sdk/0.2.163/),
[Codex](https://pypi.org/project/openai-codex/0.160.0/),
[Codex CLI](https://pypi.org/project/openai-codex-cli-bin/0.160.0/), and
[Antigravity](https://pypi.org/project/google-antigravity/0.1.20/) distributions.
