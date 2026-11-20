from __future__ import annotations

import subprocess
import sys
from textwrap import dedent


def test_core_import_does_not_import_vendor_sdks() -> None:
    # Other tests intentionally load the real SDKs. Measure the core import
    # in a fresh interpreter, with the same installed extras as this test run.
    subprocess.run(
        [
            sys.executable,
            "-c",
            dedent("""
            import sys
            import agent_runtime_kit

            assert agent_runtime_kit.AgentRuntimeKind.FAKE.value == "fake"
            assert "claude_agent_sdk" not in sys.modules
            assert "openai_codex" not in sys.modules
            assert "google.antigravity" not in sys.modules
        """),
        ],
        check=True,
    )
