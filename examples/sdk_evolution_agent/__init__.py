"""Local SDK evolution agent for agent-runtime-kit dogfooding."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

__all__ = ["RunOptions", "run_agent"]


def __getattr__(name: str) -> Any:
    """Lazily expose CLI helpers without importing every submodule."""

    if name in __all__:
        from examples.sdk_evolution_agent.cli import RunOptions, run_agent

        exports = {"RunOptions": RunOptions, "run_agent": run_agent}
        return exports[name]
    raise AttributeError(name)
