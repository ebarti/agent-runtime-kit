# October 17 SDK integration review

This upgrade starts at `3fd68b0` and targets the latest stable releases observed
on October 17, 2026: Claude Agent SDK `0.2.168`, Codex SDK and its exactly coupled
CLI `0.163.0`, and Antigravity `0.1.22`. The Codex upper bound becomes `<0.164`;
its `0.157.1` floor, Python 3.10 support, and optional vendor extras remain.

## Upstream changes and adapter decisions

| Observed change | Existing behavior and decision |
| --- | --- |
| Claude `0.2.163` to `0.2.168` adds typed hook outputs, hook context, init/session payloads, and corrects the `SandboxIgnoreViolations` type. | Already supported or outside the task contract. The adapter consumes assistant/user/result messages and constructs unchanged task options. It does not configure vendor hooks or sandbox violation-reporting exceptions. Keep SDK-owned lifecycle and typed result handling. |
| Codex `0.160.0` to `0.163.0` expands generated protocol models and notifications, including thread read state and browser/prediction data. | The handwritten `AsyncCodex`/`AsyncThread` client implementation is unchanged. `CodexConfig`'s signature differs only in the default `client_version`. Existing task execution, schemas, model/effort, and permission mappings remain applicable. Thread UI state, browser extension setup, and prediction APIs are outside this execution API. |
| Codex CLI removes bundled zsh and changes resume/permission behavior. | Already delegated to the pinned SDK runtime. The adapter does not select the removed shell binary and explicitly supplies task permission settings when starting or resuming threads. Verify structured execution, process reuse, and read-only resume with the final bundled CLI. |
| Live Code Mode execution is absent from typed SDK turn items, while a direct shell call emits command items. The identical structured-resume check reproduces this on both `0.160.0` and `0.163.0`. | Document the existing visibility boundary. Tool audit normalization is best effort over SDK-provided items; preserve that supported input boundary rather than scraping local session files or disabling vendor tools. Treat exhaustive Code Mode audit coverage as unverified. |
| Antigravity `0.1.22` replaces the `start_subagent` enum value with `invoke_subagent`, retaining deprecated constructor coercion. | Adapt now. Enum iteration previously rejected the supported alias before the SDK could normalize it. Resolve allow/deny entries with the SDK enum constructor and keep unknown-name refusals. Prefer the canonical member when deriving delegation, falling back to the old member on older SDKs. |
| Antigravity adds `run_workflow` to its nondestructive/default tool collection and gates it with `enable_subagents`. | Adapt now. Derive that gate from both selected invocation and workflow tools, including after deny-list subtraction. An explicit workflow allow-list must work; denying invocation alone must not disable an independently selected workflow. Preserve strict/read-only refusal of delegation and SDK-owned default collections. |
| Antigravity adds beta workflow/Interactions APIs, inline skills, subagent skill configuration, and model output-token options. | Outside the portable task contract: the kit runs tasks through `Agent.chat`, and does not own workflow orchestration, subagent configuration, or provider model policy. Keep those vendor APIs available through the native SDK instead of adding a second orchestration abstraction. |
| Antigravity adds eager MCP loading and payload byte limits, bulk hooks/call IDs, and fixes empty-ID tool deduplication. | Stable stdio MCP launch settings and typed `ToolCall`/`ToolResult` streams remain compatible. The adapter does not register vendor hooks or deduplicate chunks; it already records every yielded request. MCP context-loading/truncation policy is outside `McpServerConfig`'s launch-settings contract. No new portable fields are required for this upgrade. |
| Claude CLI, Codex binaries/helpers, and Antigravity local harness change. | Preserve the distinction between distribution fingerprints and runtime internals. Python source/configuration probes cannot establish binary behavior or substitute for live execution. |

## Evidence and verification

The initial repository evolution run covered eight exact API snapshots, four
baseline/candidate comparisons, and fifteen recent-release implementation
snapshots without import errors. All four release-note sources were found;
the existing behavior probes had no failures or skips. The current Codex bound
selected `0.160.1`, while independent discovery and prospective resolution
selected the exactly coupled `0.163.0` pair.

Additional credential-scrubbed construction probes against the exact
Antigravity `0.1.22` wheel reproduced rejected legacy aliases and a disabled
workflow-only delegation gate. Regression tests exercise the actual adapter
with old and canonical-only fake enums, real installed SDK constructors,
allow/deny lists, and strict/read-only refusal. No Antigravity provider call is
needed for those configuration checks.

Declarations, lockfile, and compatibility manifest are applied together by the
runner's atomic transaction after the adapter repair. Reanalysis through the
final locked Codex runtime and cumulative-diff review are required after the
update; a pre-change architecture pass alone is insufficient.

The final all-extras suite passes on Python 3.10: 603 tests passed, three live
provider tests skipped, with 90.37% coverage. A separate live bundled-Codex
check confirms structured output, same-session resume, process reuse, retained
context, and a controlled read-only write denial. The attempted Code Mode call
and operating-system denial are present in its controlled session log, but
the assertion that every tool attempt appears in the normalized audit did not
pass: neither the SDK result nor its supported thread-read includes that call.
The identical structured-resume harness also fails that completeness assertion
with the original `0.160.0` SDK/CLI, confirming the limit predates this upgrade.
A separate supported turn-notification check produced direct command items.
This is an explicit coverage limit, not evidence of a permission bypass or a
claim of complete Code Mode auditing. Live Claude and Antigravity provider
execution remain outside the local configuration checks.

Sources: [Claude SDK changelog](https://github.com/anthropics/claude-agent-sdk-python/blob/main/CHANGELOG.md),
[Codex SDK documentation](https://developers.openai.com/codex/sdk),
[Codex 0.163.0 release](https://github.com/openai/codex/releases/tag/rust-v0.163.0),
[Antigravity 0.1.21 notes](https://github.com/google-antigravity/antigravity-sdk-python/discussions/250),
and [Antigravity 0.1.22 notes](https://github.com/google-antigravity/antigravity-sdk-python/discussions/255).
