# API stability & versioning

`agent-runtime-kit` is pre-1.0 (`0.x`) and follows semantic versioning with the
usual pre-1.0 caveat: **breaking changes may land in a minor release** (`0.N`)
while the API is being shaped toward 1.0. Patch releases (`0.N.P`) are additive
or bug-fix only.

## Public API

The supported surface is exactly what `agent_runtime_kit.__all__` exports (and
the vendor adapters under `agent_runtime_kit.adapters`). Anything whose module or
name begins with an underscore (`agent_runtime_kit._types`,
`agent_runtime_kit._runtime`, etc.) is internal and may change without notice —
import the names from the top-level package instead.

`agent_runtime_kit.testing` is public and intended for downstream test suites
(fake runtimes and event sinks).

## Compatibility guarantees within a 0.x line

- **Runtime kinds are open.** `AgentRuntimeKind.coerce` accepts namespaced
  strings (e.g. `"x-myorg-agent"`), and the registry stores them, so a third
  party can ship an adapter for a new runtime without forking the enum.
- **Every runtime exposes the async lifecycle** (`aclose`, `async with`) declared
  by the `AgentRuntime` protocol. Stateless runtimes implement it as a no-op.
- **`finish_reason` values** come from `FinishReason`. The field is typed `str`
  for forward-compatibility, so new reasons can be added without a type break;
  compare against `FinishReason` members rather than bare literals.
- **Result/task mappings are read-only at the top level.** `AgentTask`/`AgentResult`
  copy `Mapping` fields into read-only dicts at construction; in-place writes to
  the mapping itself raise `TypeError`. The freeze is shallow — nested containers
  are not copied or frozen — so treat the whole structure as immutable by
  convention and compare by value. The wrappers remain plain `dict` subclasses,
  so `dataclasses.asdict`, `pickle`, `copy.deepcopy`, and JSON serialization
  keep working.
- **Constructors enforce domain invariants.** Goals and identifiers are
  non-blank; execution hints are positive; counts and monetary values are
  finite and non-negative; session start and resume inputs are mutually
  exclusive; MCP server names and tool filters are unambiguous.
- **Unknown telemetry is not zero.** Every `Usage` field is nullable. `None`
  means the provider did not report a value; `0` and `0.0` mean it explicitly
  reported zero.
- **Unsupported inputs raise, they are not dropped.** An adapter that cannot honor
  a task field raises `UnsupportedTaskInputError`; the one exception is
  vendor-option drift, which is recorded in
  `AgentResult.metadata["dropped_options"]` instead.
- **Task support can be inspected without dispatch.** `validate_task(runtime,
  task)`, `RuntimeRegistry.validate_task_for(...)`, and `AgentKit.validate_task(...)`
  return every statically detectable incompatibility as a `TaskSupportReport`.
  `TaskSupportProvider` is an optional extension, not a new requirement on the
  `AgentRuntime` protocol, so existing third-party runtimes retain structural
  compatibility and use capability-based fallback checks.
- **Package availability and execution readiness are separate.** Synchronous
  `availability()` is package-only and side-effect-free. The bounded async
  `check_readiness()` helper uses the optional `RuntimeReadinessProvider`
  extension without changing the `AgentRuntime` protocol. A third-party runtime
  without the extension maps a missing package to `NOT_READY` and present
  package to `INDETERMINATE`. `READY_TO_ATTEMPT` establishes only that known
  setup signals are present; it is not a guarantee of future execution.
- **Providers own the default model.** With no task, legacy metadata, or
  `default_model=` override, built-in adapters omit the SDK model option and let
  the provider's supported configuration select it. The effective precedence is
  task field, metadata alias, constructor override, provider-native. Result
  metadata always records `model_source` and records `model` only when known.
  A configured `supported_models` allow-list requires an explicit/verifiable
  selection and fails closed on provider-native selection.
- **`AgentKit` is sugar, not a second API.** The hub assembles the same frozen
  `AgentTask` and returns the same `AgentResult` the runtimes produce
  (`ParsedResult` is a runtime-identical subclass adding only the typed
  `parsed` accessor). Kind aliases are limited to the documented
  `KIND_ALIASES` mapping; exact kind strings always work. `output_type=`
  supports a bounded, documented subset of the typing system and raises
  `OutputTypeError` on anything outside it (Pydantic-style models are used
  through their own `model_json_schema`/`model_validate(strict=True)` when
  present). Raw schemas are checked at construction and again at dispatch
  because their nested values are only shallow-frozen. Every built-in adapter
  validates returned structured values locally.
- **Structured-output presence is explicit.** `parsed_output_available` is true
  for a validated payload, including JSON `null`. Existing non-null custom
  runtime results opt in automatically; a custom runtime returning valid null
  must set the flag explicitly.

## Vendor SDK version policy

The vendor SDK extras are pinned with cautious upper bounds (e.g.
`claude-agent-sdk>=0.2.87,<0.3`) because those SDKs are themselves pre-1.0 and
have shipped breaking changes within a minor series. The bounds are raised
deliberately — after the contract tests and the SDK-evolution agent verify a new
version — rather than left unbounded. A weekly CI lane installs the latest vendor
SDKs *within the declared caps*, so drift inside an allowed range surfaces before
it reaches installed users; a release above a cap is by design invisible to that
lane until the cap is raised. A separate lane installs every direct dependency at
its declared floor so a stale minimum cannot sit undetected in the metadata.

`COMPATIBILITY_MANIFEST` is the machine-readable form of that policy. Each entry
records the install extra, import module, accepted SDK range, exact lockfile
version exercised by the repository, and any separately versioned runtime binary
(currently `openai-codex-cli-bin`). The manifest is tested against both
`pyproject.toml` and `uv.lock`; it is evidence of the committed test baseline,
not a claim that every version in the accepted range was exhaustively tested.

## Deprecation

When a public name is slated for removal it will be kept working for at least one
minor release with a `DeprecationWarning` before it is dropped.
