"""Source snapshots distinguish a replaced class from a removed public name."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.sdk_evolution_agent.snapshots import (
    _SNAPSHOT_SCRIPT,
    diff_implementation_snapshots,
    snapshot_current_api,
)


def test_public_class_replaced_by_alias_is_changed_in_both_snapshot_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "alias_snapshot_sdk"
    package.mkdir()
    source = package / "__init__.py"
    source.write_text("class OldType:\n    pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    before = snapshot_current_api("alias-snapshot-sdk", version="1.0")
    source.write_text(
        "class NewType:\n    pass\n\nOldType = NewType\n\n"
        "def factory():\n    PrivateLocal = NewType\n    return PrivateLocal\n",
        encoding="utf-8",
    )
    after = snapshot_current_api("alias-snapshot-sdk", version="2.0")
    diff = diff_implementation_snapshots(before, after)
    old_name = "alias_snapshot_sdk/__init__.py:OldType"

    assert old_name in diff.definitions_changed
    assert old_name not in diff.definitions_removed
    assert any(
        item.name == old_name and item.kind == "alias" for item in after.implementation_definitions
    )
    assert not any("PrivateLocal" in item.name for item in after.implementation_definitions)

    env = dict(os.environ, PYTHONPATH=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-c", _SNAPSHOT_SCRIPT, "alias-snapshot-sdk", "2.0", "alias_snapshot_sdk"],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    isolated = json.loads(result.stdout)["implementation_definitions"]
    assert any(item["name"] == old_name and item["kind"] == "alias" for item in isolated)
    assert not any("PrivateLocal" in item["name"] for item in isolated)
