"""Event sinks used by the SDK evolution agent."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class JsonlEventSink:
    """Append normalized agent-runtime-kit events to a JSONL file."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    async def emit(self, event: Mapping[str, Any]) -> None:
        """Write one event."""

        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(dict(event), sort_keys=True, default=str))
            handle.write("\n")
