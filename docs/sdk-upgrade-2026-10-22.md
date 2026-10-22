# October 22 SDK integration review

This review starts at `3e5caf0` and checks all four monitored packages against
independent upstream discovery on October 22, 2026. The only newer stable
candidate is Claude Agent SDK `0.2.172`, replacing tested `0.2.168` and its
bundled Claude CLI `2.1.301` with CLI `2.1.308`. Codex SDK and its exactly
coupled CLI remain `0.163.0`; Antigravity remains `0.1.22`.

Existing dependency ranges already admit these versions. The atomic upgrade
updates the lockfile and Claude's tested version in the compatibility manifest;
no declaration widening, provider API changes, or new runtime policy is needed.

## Upstream changes and integration decisions

| Observed implementation change | Existing behavior and decision |
| --- | --- |
| Claude `0.2.168` to `0.2.170` repairs transcript-chain reconstruction so history queries recover parallel tool calls and results. | The adapter consumes live `query()` / `ClaudeSDKClient` messages and never calls `get_session_messages`, `get_session_messages_from_store`, or `SessionStore`. Keep the vendor-owned history implementation; this change requires no adapter parser or public API expansion. |
| Claude clarifies that `SessionStart` and `Setup` hook events are independent of `include_hook_events`. | The adapter does not configure this flag or expose vendor hook lifecycle messages. The option's constructor/API shape is unchanged. No change to task events is justified. |
| Claude's releases update version metadata and the bundled CLI through `2.1.308`. | Use the SDK-bundled CLI and retain the adapter's existing permission, structured-output, session, MCP, model/effort, and cleanup mappings. Python snapshots prove wrapper compatibility; they do not reveal binary internals or establish live provider behavior. |
| Recent Codex `0.162.0` to `0.163.0` history adds generated thread read-state and UI notification types; the handwritten client remains unchanged. | Existing execution and permission contracts are already supported. Thread UI state is outside this task runtime. Preserve SDK/CLI coupling at `0.163.0` and best-effort audit normalization over SDK-provided items. CLI implementation remains opaque. |
| Recent Antigravity `0.1.20` to `0.1.22` history adds beta Interactions/workflows, skills and hook/MCP/event-processing changes. | The October 17 adaptation already handles canonical invocation names, the legacy alias, and independent workflow delegation after tool filtering. Existing `Agent.chat`, configuration, MCP launch, and event translation contracts pass. Workflow orchestration, subagent skills and model policy stay in the native SDK; no additional portable fields are justified. |

## API comparison environments

Fresh isolated installations of both exact Claude SDK versions produce an empty
API diff. Comparing the installed locked baseline with a fresh candidate instead
reports four changed MCP exports: `CallToolResult`, `SdkMcpTool`, `Tool`, and
`tool`. The installed graph uses `mcp==1.27.2`; the fresh graph imports types
from `mcp_types._types`. These are environment-dependent transitive signatures,
not changes in the inspected Claude implementation.

A controlled candidate overlay retaining `mcp==1.27.2` and
`pydantic==2.13.4` reproduces all four baseline signatures. Both raw comparisons
are retained. The kit configures stdio MCP servers and does not construct these
four SDK helper exports; exact-version configuration probes and existing
adapter/SDK contract checks pass in the respective environments.

## Evidence and verification

The evidence pass includes five exact-version API snapshots, the exact Claude
`0.2.168` to `0.2.172` comparison, thirteen implementation snapshots and nine
recent-release intervals across all four monitored packages. Behavior checks
construct real provider configurations and pass with no failures or skips.
All 74 candidate adapter/real-SDK contract checks pass before promotion. The
candidate's bundled executable launches and reports Claude CLI `2.1.308`.

Direction, architecture and proposal review use the repository evolution runner
with `gpt-6.1-sol` at maximum reasoning effort. The architecture permits the
compatible dependency refresh and the independent proposal review passes.

Final-lock validation passes on Python 3.10 with all optional SDKs installed:
603 tests pass, three optional live-provider tests are skipped, and coverage is
90.37%. The lock check, Ruff, mypy and whitespace checks pass. A separate live
check through the locked bundled Codex SDK/CLI confirms structured output,
same-session resume, retained context and process reuse without an executable
override.

Live Claude execution remains unverified. The bounded smoke call through
`0.2.172` fails before a model request because the configured Vertex credentials
return `invalid_grant: Invalid JWT Signature`. An identical call through the
previous `0.2.168` SDK reproduces the same `ResultError`; the evidence establishes
an existing authentication blocker. Credentials and provider selection are not
changed. Antigravity validation covers real configurations and adapter contracts;
live provider execution was not performed.

Sources: [Claude SDK changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md),
[Claude SDK 0.2.172](https://pypi.org/project/claude-agent-sdk/0.2.172/),
[Codex SDK documentation](https://learn.chatgpt.com/docs/codex-sdk),
and the [October 17 integration review](sdk-upgrade-2026-10-17.md).
