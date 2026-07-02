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
- **Unsupported inputs raise, they are not dropped.** An adapter that cannot honor
  a task field raises `UnsupportedTaskInputError`; the one exception is
  vendor-option drift, which is recorded in
  `AgentResult.metadata["dropped_options"]` instead.

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

## Deprecation

When a public name is slated for removal it will be kept working for at least one
minor release with a `DeprecationWarning` before it is dropped.
