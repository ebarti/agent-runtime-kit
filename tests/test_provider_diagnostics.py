from __future__ import annotations

from agent_runtime_kit.adapters.diagnostics import collect_provider_diagnostics


def test_collect_provider_diagnostics_shape() -> None:
    diagnostics = collect_provider_diagnostics()

    assert {item.kind.value for item in diagnostics} == {
        "antigravity-agent-sdk",
        "claude-agent-sdk",
        "codex-agent-sdk",
    }
    assert all(item.package for item in diagnostics)
