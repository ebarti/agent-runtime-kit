# Stack Research

**Domain:** Python multi-vendor agent runtime SDK
**Researched:** 2026-06-10
**Confidence:** HIGH

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

```bash
# Core library, no vendor SDKs
pip install agent-runtime-kit

# Individual adapters
pip install "agent-runtime-kit[claude]"
pip install "agent-runtime-kit[codex]"
pip install "agent-runtime-kit[antigravity]"

# All first-party adapters
pip install "agent-runtime-kit[all]"
```

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

**If users only need one provider:**
- Install only the matching extra.
- Runtime registry should report unavailable adapters cleanly.

**If users want a portable multi-provider tool:**
- Install `agent-runtime-kit[all]`.
- Use capability checks before passing MCP, session resume, output schema, or permissive tool permissions.

**If Mestre adopts the package:**
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

---
*Stack research for: Python multi-vendor agent runtime SDK*
*Researched: 2026-06-10*
