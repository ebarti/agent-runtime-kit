# November 7 SDK integration review

This review starts at `32dbd8c` and compares the locked SDKs with independently
published candidates. It updates Claude Agent SDK `0.2.172` to `0.2.179`,
Codex SDK and its exactly required CLI `0.163.0` to `0.167.0`, and Antigravity
`0.1.22` to `0.1.23`. The Codex upper bound becomes `<0.168`; the other SDK
ranges already admit their candidates.

## Upstream changes and integration decisions

| Observed change | Existing behavior and decision |
| --- | --- |
| Claude's `HookMatcher` adds an `on_failure` option, and the bundled Claude Code CLI moves from `2.1.308` to `2.1.319`. The CLI release history includes fixes affecting non-interactive sessions, MCP, permissions, and failure reporting. | The adapter does not register SDK hook callbacks and continues to pass task permissions, MCP servers, sessions, and result messages to the SDK. Keep those mappings and the SDK-owned CLI behavior; do not claim hook support from this update. The SDK's `0.2.179` release itself contains no change notes. |
| Codex's generated app-server protocol adds lifecycle fields and image input/result variants. Its former `ImageGenerationThreadItem` class is replaced by an alias to `ResultThreadItem`; the name remains available. The handwritten Python client is unchanged. | The adapter does not depend on that generated class name. Its task, session, permission, and tool-audit mappings continue to consume the SDK's supported results. Preserve exact SDK/CLI coupling and best-effort audit coverage; do not infer binary internals from artifact fingerprints. |
| Antigravity adds workflow tool execution and custom instruction titles, and moves path and structure conversion helpers into shared modules. | The adapter passes its system prompt as a string and continues using `Agent.chat` and the SDK's configuration and policy interfaces. Workflow orchestration remains vendor-owned, although callers can enable its built-in workflow tool. The local workflow regression exercises nested tool filtering with the adapter's actual allow-list and deny-list configurations. |
| Claude Code release notes are published separately from the SDK, and a static class-definition scan can mistake a replacement alias for a removed name. | The evolution runner now verifies the bundled CLI version against the exact inspected SDK distribution, records the official CLI release interval separately from SDK notes, and blocks incomplete or contradictory evidence. Both snapshot paths record capitalized module-level aliases alongside definitions. These static observations do not prove runtime behavior. |

## Evidence and limits

The pre-update evidence includes all four exact baseline-to-candidate API diffs,
13 recent implementation snapshots, and behavior probes for each transition.
The three adapter-contract comparisons pass; the Codex CLI distribution probe
changes only its version details. The current resolver excludes Codex `0.167.0`
until the upper bound is widened, while the prospective resolver selects the
exact SDK/CLI pair. The Claude SDK changelog has no matching `0.2.179` entry;
separate official Claude Code releases cover the bundled CLI interval.

The Antigravity workflow test uses the real `0.1.23` native harness and a local
fake OpenAI-compatible model endpoint. A positive control creates a temporary
file when the adapter's selected tools permit it; the same nested call is
refused for both an allow-list excluding file creation and a deny-list naming
it. This is a local semantic check of those cases, not a live provider test or
a claim about all possible tools and policies.

Final-lock verification on Python 3.10 passed the lock check, Ruff, and mypy.
The core suite passed 587 tests with 27 skips; the all-extras suite passed 611
tests with three live-provider skips and 90.37% coverage. A separate live call
through the locked Codex SDK and bundled CLI verified structured output,
session resume, retained context, and process reuse. The post-update evolution
run also passed its review through that SDK/CLI pair without an executable
override.

Claude's bundled CLI, the Codex executable, and Antigravity's native harness
are opaque in the inspected distributions. API/configuration probes and source
fingerprints cannot establish all runtime behavior. Live Claude and Antigravity
provider calls were not run; the workflow check above uses the local harness and
a fake model endpoint.

Sources: [Claude SDK release](https://github.com/anthropics/claude-agent-sdk-python/releases/tag/v0.2.179),
[Claude Code CLI release](https://github.com/anthropics/claude-code/releases/tag/v2.1.319),
[Codex release](https://github.com/openai/codex/releases/tag/rust-v0.167.0),
and [Antigravity release notes](https://github.com/google-antigravity/antigravity-sdk-python/discussions/273).
