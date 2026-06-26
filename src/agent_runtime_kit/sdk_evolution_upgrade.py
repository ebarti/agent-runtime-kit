"""Named entrypoint for the repo-local SDK evolution upgrade workflow."""

from __future__ import annotations

import runpy
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    """Run the checked-in SDK evolution upgrade script from a repo checkout."""

    repo_root = find_repo_root(Path.cwd())
    script = repo_root / "scripts" / "sdk_evolution_upgrade.py"
    namespace = runpy.run_path(str(script))
    script_main = namespace.get("main")
    if not callable(script_main):
        raise SystemExit(f"script has no callable main: {script}")
    return int(script_main(list(argv) if argv is not None else sys.argv[1:]))


def find_repo_root(start: Path) -> Path:
    """Find the agent-runtime-kit checkout containing the upgrade script."""

    for path in (start.resolve(), *start.resolve().parents):
        script = path / "scripts" / "sdk_evolution_upgrade.py"
        pyproject = path / "pyproject.toml"
        if script.exists() and _is_agent_runtime_kit_project(pyproject):
            return path
    raise SystemExit(
        "sdk-evolution-upgrade must be run from inside an agent-runtime-kit checkout"
    )


def _is_agent_runtime_kit_project(pyproject: Path) -> bool:
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return False
    return 'name = "agent-runtime-kit"' in text


__all__ = ["find_repo_root", "main"]
