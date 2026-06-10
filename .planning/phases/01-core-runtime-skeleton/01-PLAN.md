# Phase 1: Core Runtime Skeleton - Plan

## Plan 01-01: Package scaffolding and Python 3.10+ metadata

- Replace the uv placeholder metadata with `agent-runtime-kit`.
- Use a `src/` package layout and include `py.typed`.
- Add dev tooling configuration for ruff, mypy, and pytest.

## Plan 01-02: Public task/result/runtime/capability/error models

- Add enums, dataclasses, and protocols for the public runtime contract.
- Add typed runtime errors for missing runtimes and unsupported task inputs.
- Preserve vendor-specific extension space through metadata fields.

## Plan 01-03: Registry, availability diagnostics, lint, and static checks

- Add a runtime registry and dependency-free fake runtime.
- Add tests that prove core import and fake execution work without vendor SDKs.
- Run unit tests, lint, and static analysis.
