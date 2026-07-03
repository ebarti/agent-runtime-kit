"""Local SDK evolution agent for agent-runtime-kit dogfooding."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

_REPO_SRC = Path(__file__).resolve().parents[2] / "src"
if _REPO_SRC.exists() and str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

__all__ = ["RunOptions", "run_agent"]

if TYPE_CHECKING:
    from examples.sdk_evolution_agent.cli import run_agent
    from examples.sdk_evolution_agent.models import RunOptions


def __getattr__(name: str) -> Any:
    """Lazily expose CLI helpers without importing every submodule."""

    if name in __all__:
        from examples.sdk_evolution_agent.cli import run_agent
        from examples.sdk_evolution_agent.models import RunOptions

        exports = {"RunOptions": RunOptions, "run_agent": run_agent}
        return exports[name]
    raise AttributeError(name)
