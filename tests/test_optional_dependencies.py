from __future__ import annotations

import sys

import agent_runtime_kit


def test_core_import_does_not_import_vendor_sdks() -> None:
    assert agent_runtime_kit.AgentRuntimeKind.FAKE.value == "fake"
    assert "claude_agent_sdk" not in sys.modules
    assert "openai_codex" not in sys.modules
    assert "google.antigravity" not in sys.modules
