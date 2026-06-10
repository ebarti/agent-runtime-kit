# Phase 5: Public Release Readiness - Context

**Gathered:** 2026-06-10
**Status:** Ready for planning

<domain>
## Phase Boundary

Prepare the package for a first public PyPI release with README, provider docs,
capability matrix, live smoke guidance, build verification, license, and
publish checklist.

</domain>

<decisions>
## Implementation Decisions

### Documentation
- README should explain what the package is and is not.
- Provider docs should document capability differences rather than hiding them.
- Capability matrix should be concise and release-facing.

### Release Verification
- Live smoke tests must be skipped unless explicit environment flags are set.
- Publish checklist must include a fresh PyPI name check.
- Build verification must produce wheel and sdist successfully.

### the agent's Discretion
Keep release material practical and avoid promising hosted infrastructure or
full orchestration features.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- Phase 1-4 package, adapter, test, and docs structure is in place.

### Established Patterns
- Tests use explicit opt-in for provider work that needs credentials.

### Integration Points
- `docs/publish-checklist.md` is the final release gate.

</code_context>

<specifics>
## Specific Ideas

The PyPI name `agent-runtime-kit` was rechecked during this phase and returned
404/FREE on 2026-06-10.

</specifics>

<deferred>
## Deferred Ideas

Actual PyPI publication is deferred until after review/merge.

</deferred>
