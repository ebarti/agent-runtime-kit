<!-- GSD:project-start source:PROJECT.md -->

## Project

**agent-runtime-kit**

`agent-runtime-kit` is a Python package that gives developers one clean API for running agentic
coding tasks through Claude Agent SDK, OpenAI Codex SDK, and Google
Antigravity SDK. It extracts the useful vendor-runtime ideas from Mestre while
remaining independently useful to the community: install it, choose a runtime,
run a task, stream/inspect events, and get a typed result back.

The package is not a new orchestrator or model router. It is the reusable
runtime layer that makes vendor agent SDKs feel consistent without hiding the
capabilities and constraints that make each SDK different.

**Core Value:** Developers can run the same agentic task through Claude, Codex, or Antigravity
using one small, typed Python API while preserving the vendor-specific
capabilities needed for real work.

### Constraints

- **Language**: Python package first - Mestre and all three target vendor SDK
  integrations are Python-facing for this work.

- **Python version**: Python 3.10+ - broad community compatibility matters more
  than matching Mestre's current Python 3.14-only project constraint.

- **Package name**: Use `agent-runtime-kit` unless a later publishing check
  shows the name is no longer available.

- **Vendor support**: Claude, Codex, and Antigravity must all be runnable in
  v1; partial provider stubs are not enough for a useful community release.

- **Dependency model**: Vendor SDKs should be optional extras so users can
  install only the runtimes they need.

- **Architecture**: Extract the runtime/adapters layer from Mestre, not the full
  orchestration and routing system.

- **API design**: Prefer a clean public API, but keep compatibility adapters or
  migration helpers so Mestre can adopt the package without excessive churn.

- **Authentication**: Stay within supported vendor SDK authentication
  mechanisms; do not build brittle local credential scraping into the core.
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->

## Technology Stack

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | >=3.10 | Package runtime | Claude Agent SDK, Codex SDK, and Google Antigravity all currently advertise Python 3.10+ compatibility. |
| Pydantic | 2.13.4 current; use >=2.12 | Public request/result validation where useful | Mature typed validation without forcing callers into framework-specific models. |
| claude-agent-sdk | 0.2.96 current | Claude runtime adapter | Official Agent SDK for Claude Code-style local agent execution. |
| openai-codex | 0.1.0b3 current | Codex runtime adapter | Official Python SDK for Codex app-server integration. |
| openai-codex-cli-bin | 0.136.0 current | Codex runtime dependency | Pinned Codex CLI runtime used by the Python SDK package. |
| google-antigravity | 0.1.2 current | Antigravity runtime adapter | Official Google Antigravity Python SDK for local agent harness integration. |
| anyio or asyncio | stdlib plus optional anyio | Async runtime compatibility | Vendor SDKs are async; the public API should be async-first. |
| OpenTelemetry API | 1.42.1 current | Optional event/trace integration | Mestre already normalizes agent events into span-event-shaped payloads; community users will expect observability hooks. |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| typing-extensions | latest compatible | Backport typing features | Needed if the public API uses newer typing forms while supporting 3.10. |
| jsonschema | >=4.0 | Structured output validation | Useful for validating schema-constrained vendor responses in tests and runtime. |
| pytest | >=8.0 | Test runner | Unit tests with fake SDK surfaces and async adapter tests. |
| pytest-asyncio or anyio pytest plugin | latest compatible | Async tests | Required for adapter and stream tests. |
| ruff | >=0.9 | Lint and format | Matches Mestre's style and is fast for a public package. |
| hatchling or uv build backend | current | Packaging | Simple pyproject-based packaging and optional extras. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv | Dependency resolution and local workflows | Existing project uses `uv.lock`; keep the package easy to develop with uv. |
| ruff | Lint/format | Enforce import order, pyupgrade, bugbear, and style early. |
| pytest | Tests | Fake SDK modules should cover common drift and missing dependency paths. |
| pyright or basedpyright | Type checking | Add after core types stabilize; useful for public API credibility. |
| twine / trusted publishing | PyPI release | Use trusted publishing if possible; recheck `agent-runtime-kit` immediately before first publish. |

## Installation

# Core library, no vendor SDKs

# Individual adapters

# All first-party adapters

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| Optional extras per vendor | Hard dependency on all SDKs | Only if the package is explicitly a batteries-included CLI, which this is not. |
| Async-first API | Sync wrapper only | Sync wrappers can be added later; vendor SDKs and streaming surfaces are naturally async. |
| Runtime contract plus adapters | Full orchestration framework | Use full orchestration only in applications like Mestre; it is too broad for the package core. |
| Pydantic-light public models | Dataclasses only | Dataclasses are fine for the minimal core, but Pydantic helps validation and schema ergonomics if kept optional or contained. |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| Lowest-common-denominator abstraction | It hides important differences such as MCP support, session resume, permissions, and structured output support. | Capability declarations and explicit unsupported-feature errors. |
| Mandatory installation of every vendor SDK | Bloats installs and breaks users who only want one provider. | Optional extras. |
| Unsupported local credential scraping | Vendor auth behavior is sensitive and changes; unsupported scraping creates security and trust issues. | Supported SDK auth paths and clear docs. |
| Mestre-specific routing/model policy in core | Makes the package a Mestre extraction rather than a reusable community SDK. | Keep policy in Mestre; expose hooks for callers to choose runtimes. |
| Pinning to Mestre's current SDK versions forever | Vendor SDKs are actively changing. | Version ranges plus fake-SDK contract tests and compatibility docs. |

## Stack Patterns by Variant

- Install only the matching extra.
- Runtime registry should report unavailable adapters cleanly.
- Install `agent-runtime-kit[all]`.
- Use capability checks before passing MCP, session resume, output schema, or permissive tool permissions.
- Add a Mestre compatibility adapter that maps Mestre's `AgentTask` and `AgentResult` to public package models.
- Keep Mestre's model routing, fallback, benchmark, and optimization layers outside this package.

## Version Compatibility

| Package | Current Version Checked | Python | Notes |
|---------|-------------------------|--------|-------|
| claude-agent-sdk | 0.2.96 | >=3.10 | Newer than Mestre's pinned 0.2.91; tests must detect option-surface drift. |
| openai-codex | 0.1.0b3 | >=3.10 | Beta package; isolate Codex SDK API drift behind adapter boundaries. |
| openai-codex-cli-bin | 0.136.0 | >=3.10 | Runtime dependency for Codex SDK. |
| google-antigravity | 0.1.2 | >=3.10 | Includes compiled runtime wheels; install from PyPI rather than source checkout. |
| google-genai | 2.8.0 | >=3.10 | Not a core runtime adapter dependency unless Antigravity or future Google direct paths need it. |

## Sources

- https://docs.anthropic.com/en/docs/claude-code/sdk - Claude Agent SDK overview, auth, built-in tools, permissions, sessions.
- https://docs.anthropic.com/en/docs/claude-code/sdk/sdk-python - Claude Agent SDK Python API reference.
- https://developers.openai.com/codex/sdk - Codex SDK documentation.
- https://github.com/google-antigravity/antigravity-sdk-python - Google Antigravity Python SDK README and examples.
- https://pypi.org/project/claude-agent-sdk/ - current package metadata.
- https://pypi.org/project/openai-codex/ - current package metadata.
- https://pypi.org/project/google-antigravity/ - current package metadata.
- `~/Github/mestre/pyproject.toml` - current Mestre dependency pins.
- `~/Github/mestre/mestre/vendor_lane/*` - current source implementation to extract from.

<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->

## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->

## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->

## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->

## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:

- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->

## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
