"""Local SDK evolution agent for agent-runtime-kit dogfooding."""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from examples.sdk_evolution_agent.cli import RunOptions, run_agent  # noqa: E402

__all__ = ["RunOptions", "run_agent"]
