"""Release-note evidence collection for SDK evolution runs."""

from __future__ import annotations

import re
import urllib.request
from collections.abc import Callable, Mapping, Sequence

from examples.sdk_evolution_agent.models import ReleaseNoteEvidence, SourceRef

ReleaseNoteFetcher = Callable[[str], str]

RELEASE_NOTE_SOURCES: dict[str, tuple[SourceRef, ...]] = {
    "claude-agent-sdk": (
        SourceRef(
            kind="changelog",
            label="Claude Agent SDK Python changelog",
            url=(
                "https://raw.githubusercontent.com/anthropics/"
                "claude-agent-sdk-python/main/CHANGELOG.md"
            ),
        ),
        SourceRef(
            kind="docs",
            label="Claude Agent SDK overview",
            url="https://code.claude.com/docs/en/agent-sdk/overview",
        ),
    ),
    "openai-codex": (
        SourceRef(
            kind="docs",
            label="Codex SDK docs",
            url="https://developers.openai.com/codex/sdk",
        ),
        SourceRef(
            kind="changelog",
            label="Codex changelog",
            url="https://developers.openai.com/codex/changelog",
        ),
        SourceRef(
            kind="release",
            label="Codex repository releases",
            url="https://github.com/openai/codex/releases",
        ),
    ),
    "openai-codex-cli-bin": (
        SourceRef(
            kind="release",
            label="Codex repository releases",
            url="https://github.com/openai/codex/releases",
        ),
        SourceRef(
            kind="package-metadata",
            label="Codex CLI binary package metadata",
            url="https://pypi.org/project/openai-codex-cli-bin/",
        ),
    ),
    "google-antigravity": (
        SourceRef(
            kind="repository",
            label="Antigravity SDK repository",
            url="https://github.com/google-antigravity/antigravity-sdk-python",
        ),
        SourceRef(
            kind="package-metadata",
            label="Antigravity package metadata",
            url="https://pypi.org/project/google-antigravity/",
        ),
    ),
}


def collect_release_notes(
    packages: Sequence[Mapping[str, object]],
    update_versions: Mapping[str, str],
    *,
    fetcher: ReleaseNoteFetcher | None = None,
) -> tuple[ReleaseNoteEvidence, ...]:
    """Collect primary-source release-note evidence for update candidates."""

    fetcher = fetcher or fetch_url_text
    evidence: list[ReleaseNoteEvidence] = []
    for package in packages:
        name = str(package.get("name") or "")
        if not name:
            continue
        from_version = _string_or_none(package.get("locked_version")) or _string_or_none(
            package.get("installed_version")
        )
        to_version = update_versions.get(name)
        if not to_version:
            evidence.append(
                ReleaseNoteEvidence(
                    package=name,
                    from_version=from_version,
                    to_version=None,
                    status="not-needed",
                    sources=RELEASE_NOTE_SOURCES.get(name, ()),
                    unavailable_reason="no resolver-selected update",
                )
            )
            continue

        sources = RELEASE_NOTE_SOURCES.get(name, ())
        summaries: list[str] = []
        checked_urls: list[str] = []
        source_results: list[SourceRef] = []
        failures: list[str] = []
        for source in sources:
            if not source.url:
                source_results.append(source)
                continue
            checked_urls.append(source.url)
            try:
                text = fetcher(source.url)
            except Exception as exc:
                failures.append(f"{source.label}: {exc}")
                source_results.append(
                    SourceRef(
                        kind=source.kind,
                        label=source.label,
                        url=source.url,
                        version=to_version,
                        available=False,
                        note=str(exc),
                    )
                )
                continue
            source_results.append(
                SourceRef(
                    kind=source.kind,
                    label=source.label,
                    url=source.url,
                    version=to_version,
                    available=True,
                )
            )
            summaries.extend(
                _summaries_for_interval(
                    text,
                    from_version=from_version,
                    to_version=to_version,
                )
            )

        if summaries:
            status = "found"
            unavailable_reason = ""
        elif checked_urls and len(failures) < len(checked_urls):
            status = "no-matching-version"
            unavailable_reason = "sources fetched but no matching version text was found"
        elif checked_urls:
            status = "unavailable"
            unavailable_reason = "; ".join(failures)
        else:
            status = "unavailable"
            unavailable_reason = "no release-note source configured"

        evidence.append(
            ReleaseNoteEvidence(
                package=name,
                from_version=from_version,
                to_version=to_version,
                status=status,
                sources=tuple(source_results or sources),
                summaries=tuple(_dedupe(summaries)[:8]),
                checked_urls=tuple(checked_urls),
                unavailable_reason=unavailable_reason,
            )
        )
    return tuple(evidence)


def fetch_url_text(url: str) -> str:
    """Fetch a release-note source as text."""

    request = urllib.request.Request(url, headers={"User-Agent": "agent-runtime-kit-sdk-evolution"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return response.read().decode("utf-8", errors="replace")


def _summaries_for_interval(
    text: str,
    *,
    from_version: str | None,
    to_version: str,
) -> list[str]:
    lines = [line.strip() for line in text.splitlines()]
    version_patterns = [to_version]
    if from_version:
        version_patterns.append(from_version)
    matches: list[str] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        if any(pattern and pattern in line for pattern in version_patterns):
            matches.append(_clean_summary(line))
            for nearby in lines[index + 1 : index + 4]:
                cleaned = _clean_summary(nearby)
                if cleaned:
                    matches.append(cleaned)
    if not matches and to_version:
        compact = re.sub(r"\s+", " ", text)
        version_index = compact.find(to_version)
        if version_index >= 0:
            start = max(0, version_index - 160)
            end = min(len(compact), version_index + 320)
            matches.append(_clean_summary(compact[start:end]))
    return [match for match in matches if match]


def _clean_summary(value: str, *, limit: int = 280) -> str:
    cleaned = re.sub(r"<[^>]+>", "", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -*#\t")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 14].rstrip() + " [truncated]"


def _dedupe(values: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
