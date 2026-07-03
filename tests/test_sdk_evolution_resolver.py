from __future__ import annotations

from pathlib import Path

from examples.sdk_evolution_agent.collectors import (
    read_pyproject_dependency_specs,
    read_uv_lock_versions,
    select_recent_versions,
)
from examples.sdk_evolution_agent.models import CommandResult
from examples.sdk_evolution_agent.resolver import (
    raise_upper_bounds_in_pyproject_text,
    resolve_constraint_horizon_candidates,
    resolve_update_candidates,
)


def test_read_pyproject_specs_from_optional_dependencies(tmp_path: Path) -> None:
    path = tmp_path / "pyproject.toml"
    path.write_text(
        """
[project.optional-dependencies]
# claude-agent-sdk>=9.9 is only a comment.
claude = ["claude-agent-sdk>=0.2.87,<0.3"]
all = ["openai-codex>=0.1.0b3,<0.2"]
""",
        encoding="utf-8",
    )

    assert read_pyproject_dependency_specs(path) == {
        "claude-agent-sdk": "claude-agent-sdk>=0.2.87,<0.3",
        "openai-codex": "openai-codex>=0.1.0b3,<0.2",
    }


def test_read_uv_lock_versions_toml(tmp_path: Path) -> None:
    path = tmp_path / "uv.lock"
    path.write_text(
        """
[[package]]
name = "claude-agent-sdk"
version = "0.2.110"
""",
        encoding="utf-8",
    )

    assert read_uv_lock_versions(path) == {"claude-agent-sdk": "0.2.110"}


def test_version_key_pep440_ordering() -> None:
    releases = {
        "0.1.0b3": [{}],
        "0.1.0": [{}],
        "0.134.0a1": [{}],
        "0.134.0": [{}],
        "1.0.0": [{}],
        "1.0.0.post1": [{}],
        "1.0.0.dev1": [{}],
        "1.0.0a1": [{}],
        "0.2.87": [{}],
        "0.2.110": [{}],
    }

    ordered = select_recent_versions(releases, limit=len(releases))

    assert ordered.index("0.1.0") < ordered.index("0.1.0b3")
    assert ordered.index("0.134.0") < ordered.index("0.134.0a1")
    assert ordered.index("1.0.0.post1") < ordered.index("1.0.0")
    assert ordered.index("1.0.0a1") < ordered.index("1.0.0.dev1")
    assert ordered.index("0.2.110") < ordered.index("0.2.87")


def test_resolver_never_mutates_workspace(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
version = "0.0.0"
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2,<0.3"]
""",
        encoding="utf-8",
    )
    lock = tmp_path / "uv.lock"
    lock.write_text(
        """
[[package]]
name = "claude-agent-sdk"
version = "0.2.1"
""",
        encoding="utf-8",
    )
    before = lock.read_text(encoding="utf-8")

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> CommandResult:
        del env, timeout
        assert command == ("uv", "lock", "-P", "claude-agent-sdk")
        (cwd / "uv.lock").write_text(
            """
[[package]]
name = "claude-agent-sdk"
version = "0.2.2"
""",
            encoding="utf-8",
        )
        return CommandResult(command=command, returncode=0)

    candidates, result = resolve_update_candidates(
        tmp_path, ("claude-agent-sdk",), command_runner=runner
    )

    assert result.returncode == 0
    assert lock.read_text(encoding="utf-8") == before
    assert [(item.package, item.from_version, item.to_version) for item in candidates] == [
        ("claude-agent-sdk", "0.2.1", "0.2.2")
    ]


def test_cap_rewrite_touches_only_upper_bound() -> None:
    text = (
        '[project.optional-dependencies]\n'
        'claude = ["claude-agent-sdk>=0.2.87,<0.3"]\n'
        'all = ["claude-agent-sdk>=0.2.87,<0.3"]\n'
        '[tool.uv]\n'
        'exclude-newer = "2026-01-01T00:00:00Z"\n'
    )

    updated, raised = raise_upper_bounds_in_pyproject_text(
        text, ("claude-agent-sdk",), versions={"claude-agent-sdk": "0.3.0"}
    )

    assert updated.count("claude-agent-sdk>=0.2.87,<0.4") == 2
    assert "exclude-newer" in updated
    assert raised["claude-agent-sdk"].current == "<0.3"


def test_horizon_candidates_distinguish_cap_blocked_from_cutoff_delayed(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("# x\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        """
[project]
name = "x"
version = "0.0.0"
[project.optional-dependencies]
claude = ["claude-agent-sdk>=0.2,<0.3"]
codex = ["openai-codex>=0.1.0b3,<0.2"]
[tool.uv]
exclude-newer = "2026-01-01T00:00:00Z"
""",
        encoding="utf-8",
    )
    (tmp_path / "uv.lock").write_text(
        """
[[package]]
name = "claude-agent-sdk"
version = "0.2.1"

[[package]]
name = "openai-codex"
version = "0.1.0b3"
""",
        encoding="utf-8",
    )

    def runner(
        command: tuple[str, ...],
        *,
        cwd: Path,
        env: dict[str, str],
        timeout: int,
    ) -> CommandResult:
        del command, env, timeout
        (cwd / "uv.lock").write_text(
            """
[[package]]
name = "claude-agent-sdk"
version = "0.3.0"

[[package]]
name = "openai-codex"
version = "0.1.1"
""",
            encoding="utf-8",
        )
        return CommandResult(command=("uv", "lock"), returncode=0)

    candidates = resolve_constraint_horizon_candidates(
        tmp_path,
        ("claude-agent-sdk", "openai-codex"),
        pypi_metadata={
            "openai-codex": {
                "releases": {
                    "0.1.1": [
                        {
                            "upload_time_iso_8601": "2026-01-03T00:00:00.000Z",
                        }
                    ]
                }
            }
        },
        command_runner=runner,
    )

    by_package = {candidate.package: candidate for candidate in candidates}
    assert by_package["claude-agent-sdk"].blocked_by_cap == "<0.3"
    assert by_package["claude-agent-sdk"].cutoff_delayed_until is None
    assert by_package["openai-codex"].blocked_by_cap is None
    assert by_package["openai-codex"].cutoff_delayed_until == "2026-01-11"
