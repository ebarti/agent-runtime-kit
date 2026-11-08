"""The real workflow harness must honor adapter-owned tool filters."""

from __future__ import annotations

import importlib.metadata
import os
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.version import Version


def test_programmatic_workflow_calls_honor_tool_filters(tmp_path: Path) -> None:
    try:
        version = importlib.metadata.version("google-antigravity")
    except importlib.metadata.PackageNotFoundError:
        pytest.skip("Antigravity extra is not installed")
    if Version(version) < Version("0.1.23"):
        pytest.skip("Programmatic workflow tool calls require Antigravity 0.1.23+")

    home = tmp_path / "home"
    home.mkdir()
    root = Path(__file__).resolve().parents[1]
    env = {
        "HOME": str(home),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(root / "src"),
        "TMPDIR": str(tmp_path),
    }
    for key in ("SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT"):
        if key in os.environ:
            env[key] = os.environ[key]
    result = subprocess.run(
        [sys.executable, str(root / "tests/_antigravity_workflow_probe.py"), str(tmp_path)],
        env=env,
        capture_output=True,
        text=True,
        timeout=100,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "allowed: allowed" in result.stdout
    assert "allowlist: refused" in result.stdout
    assert "denylist: refused" in result.stdout
