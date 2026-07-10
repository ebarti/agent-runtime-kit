"""Declared vendor SDK compatibility ranges for the built-in adapters."""

from __future__ import annotations

from dataclasses import dataclass

from agent_runtime_kit._types import AgentRuntimeKind


def _require_nonblank(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


@dataclass(frozen=True)
class PackageVersion:
    """An exact dependency version exercised by the committed lockfile."""

    package: str
    version: str

    def __post_init__(self) -> None:
        _require_nonblank(self.package, "PackageVersion.package")
        _require_nonblank(self.version, "PackageVersion.version")


@dataclass(frozen=True)
class RuntimeCompatibility:
    """One built-in adapter's supported range and tested dependency set."""

    kind: AgentRuntimeKind
    extra: str
    package: str
    module: str
    version_specifier: str
    tested_version: str
    tested_runtime_dependencies: tuple[PackageVersion, ...] = ()

    def __post_init__(self) -> None:
        for field in (
            "extra",
            "package",
            "module",
            "version_specifier",
            "tested_version",
        ):
            _require_nonblank(getattr(self, field), f"RuntimeCompatibility.{field}")
        dependencies = tuple(self.tested_runtime_dependencies)
        if not all(isinstance(item, PackageVersion) for item in dependencies):
            raise ValueError(
                "RuntimeCompatibility.tested_runtime_dependencies must contain "
                "only PackageVersion values"
            )
        names = tuple(item.package for item in dependencies)
        if len(names) != len(set(names)):
            raise ValueError(
                "RuntimeCompatibility.tested_runtime_dependencies contains duplicates"
            )
        object.__setattr__(self, "tested_runtime_dependencies", dependencies)


COMPATIBILITY_MANIFEST: tuple[RuntimeCompatibility, ...] = (
    RuntimeCompatibility(
        kind=AgentRuntimeKind.CLAUDE_AGENT_SDK,
        extra="claude",
        package="claude-agent-sdk",
        module="claude_agent_sdk",
        version_specifier=">=0.2.87,<0.3",
        tested_version="0.2.106",
    ),
    RuntimeCompatibility(
        kind=AgentRuntimeKind.CODEX_AGENT_SDK,
        extra="codex",
        package="openai-codex",
        module="openai_codex",
        version_specifier=">=0.1.0b3,<0.2",
        tested_version="0.1.0b3",
        tested_runtime_dependencies=(
            PackageVersion(package="openai-codex-cli-bin", version="0.137.0a4"),
        ),
    ),
    RuntimeCompatibility(
        kind=AgentRuntimeKind.ANTIGRAVITY_AGENT_SDK,
        extra="antigravity",
        package="google-antigravity",
        module="google.antigravity",
        version_specifier=">=0.1.2,<0.2",
        tested_version="0.1.4",
    ),
)


def compatibility_for(kind: AgentRuntimeKind | str) -> RuntimeCompatibility:
    """Return the manifest entry for a built-in runtime kind."""

    normalized = AgentRuntimeKind.coerce(kind)
    for entry in COMPATIBILITY_MANIFEST:
        if entry.kind is normalized:
            return entry
    raise KeyError(f"no built-in compatibility manifest entry for {normalized!r}")
