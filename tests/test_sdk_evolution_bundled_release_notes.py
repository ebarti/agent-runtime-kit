"""Exact bundled-runtime release evidence for the SDK evolution agent."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.sdk_evolution_agent.models import ApiSnapshot, ImplementationFile, to_jsonable
from examples.sdk_evolution_agent.release_notes import collect_release_notes
from examples.sdk_evolution_agent.stages import with_release_note_guard


def _snapshot(sdk_version: str, cli_source: str) -> ApiSnapshot:
    content = cli_source.encode()
    return ApiSnapshot(
        package="claude-agent-sdk",
        version=sdk_version,
        module="claude_agent_sdk",
        implementation_files=(
            ImplementationFile(
                path="claude_agent_sdk/_cli_version.py",
                kind="python",
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            ),
        ),
    )


def _collect(fetcher: Callable[[str], str]):
    return collect_release_notes(
        [{"name": "claude-agent-sdk", "locked_version": "0.2.1"}],
        {"claude-agent-sdk": "0.2.2"},
        fetcher=fetcher,
        snapshots=[
            _snapshot("0.2.1", '__cli_version__ = "2.1.1"\n'),
            _snapshot("0.2.2", '__cli_version__ = "2.1.3"\n'),
        ],
    )[0]


def test_bundled_cli_evidence_preserves_missing_sdk_notes_and_covers_interval() -> None:
    requests: list[str] = []

    def fetcher(url: str) -> str:
        requests.append(url)
        if "/v0.2.1/" in url:
            return '__cli_version__ = "2.1.1"\n'
        if "/v0.2.2/" in url:
            return '__cli_version__ = "2.1.3"\n'
        if "api.github.com/repos/anthropics/claude-code/releases?" in url:
            return json.dumps(
                [
                    {
                        "tag_name": "v2.1.4",
                        "body": "newer",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.4",
                    },
                    {
                        "tag_name": "v2.1.3",
                        "body": "MCP tasks changed",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.3",
                    },
                    {
                        "tag_name": "v2.1.2",
                        "body": "Permission handling changed",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.2",
                    },
                    {
                        "tag_name": "v2.1.1",
                        "body": "baseline",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.1",
                    },
                ]
            )
        return "Official SDK source without a matching entry"

    notes = _collect(fetcher)

    assert notes.status == "no-matching-version"
    runtime = notes.bundled_runtime
    assert runtime is not None
    assert (runtime.from_version, runtime.to_version, runtime.status) == ("2.1.1", "2.1.3", "found")
    assert [release.version for release in runtime.releases] == ["2.1.2", "2.1.3"]
    assert "MCP tasks changed" in runtime.releases[-1].notes
    assert any("v0.2.2/" in url for url in requests)


def test_unverified_bundled_version_blocks_promotion() -> None:
    def fetcher(url: str) -> str:
        if "/v0.2.1/" in url:
            return '__cli_version__ = "2.1.1"\n'
        if "/v0.2.2/" in url:
            return '__cli_version__ = "9.9.9"\n'
        if "api.github.com/repos/anthropics/claude-code/releases?" in url:
            raise AssertionError("Unverified versions must never select release evidence")
        return "Official SDK source without a matching entry"

    notes = _collect(fetcher)
    runtime = notes.bundled_runtime
    assert runtime is not None
    assert runtime.status == "unavailable"
    assert "fingerprint" in runtime.unavailable_reason.lower()
    guarded = with_release_note_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        [to_jsonable(notes)],
    )
    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


def test_missing_bundled_cli_release_body_blocks_promotion() -> None:
    def fetcher(url: str) -> str:
        if "/v0.2.1/" in url:
            return '__cli_version__ = "2.1.1"\n'
        if "/v0.2.2/" in url:
            return '__cli_version__ = "2.1.3"\n'
        if "api.github.com/repos/anthropics/claude-code/releases?" in url:
            return json.dumps(
                [
                    {
                        "tag_name": "v2.1.3",
                        "body": "",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.3",
                    },
                    {
                        "tag_name": "v2.1.1",
                        "body": "baseline",
                        "html_url": "https://github.com/anthropics/claude-code/releases/tag/v2.1.1",
                    },
                ]
            )
        return "Official SDK source without a matching entry"

    notes = _collect(fetcher)
    runtime = notes.bundled_runtime
    assert runtime is not None
    assert runtime.status == "incomplete"
    guarded = with_release_note_guard(
        {
            "findings": [],
            "safe_to_implement": True,
            "manual_design_required": False,
            "uncertainty": [],
        },
        [to_jsonable(notes)],
    )
    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


def test_release_guard_blocks_missing_bundled_runtime_evidence() -> None:
    guarded = with_release_note_guard(
        {"findings": [], "safe_to_implement": True, "manual_design_required": False},
        [{"package": "claude-agent-sdk", "to_version": "0.2.2", "status": "found"}],
    )
    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True


def test_release_index_is_paginated_until_baseline_is_covered() -> None:
    requests: list[str] = []

    def fetcher(url: str) -> str:
        requests.append(url)
        if "/v0.2.1/" in url:
            return '__cli_version__ = "2.1.1"\n'
        if "/v0.2.2/" in url:
            return '__cli_version__ = "2.1.3"\n'
        if "&page=1" in url:
            return json.dumps(
                [{"tag_name": f"v3.0.{n}", "body": "newer"} for n in range(99)]
                + [{"tag_name": "v2.1.3", "body": "candidate"}]
            )
        if "&page=2" in url:
            return json.dumps(
                [
                    {"tag_name": "v2.1.2", "body": "intermediate"},
                    {"tag_name": "v2.1.1", "body": "baseline"},
                ]
            )
        return "Official SDK source without a matching entry"

    runtime = _collect(fetcher).bundled_runtime
    assert runtime is not None
    assert runtime.status == "found"
    assert [release.version for release in runtime.releases] == ["2.1.2", "2.1.3"]
    assert any("page=2" in url for url in requests)


def test_release_guard_rejects_found_status_without_candidate_notes() -> None:
    guarded = with_release_note_guard(
        {"findings": [], "safe_to_implement": True, "manual_design_required": False},
        [
            {
                "package": "claude-agent-sdk",
                "to_version": "0.2.2",
                "status": "no-matching-version",
                "bundled_runtime": {
                    "name": "claude-code",
                    "status": "found",
                    "from_version": "2.1.1",
                    "to_version": "2.1.3",
                    "releases": [{"version": "2.1.2", "notes": "older notes"}],
                },
            }
        ],
    )
    assert guarded["safe_to_implement"] is False
    assert guarded["manual_design_required"] is True
