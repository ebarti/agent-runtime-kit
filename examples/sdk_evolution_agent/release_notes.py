"""Release-note evidence collection for SDK evolution runs."""

from __future__ import annotations

import gzip
import json
import os
import re
import urllib.parse
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
            kind="github-discussions",
            label="Google Antigravity SDK release-note discussions",
            url=(
                "https://github.com/google-antigravity/antigravity-sdk-python/"
                "discussions/categories/announcements"
            ),
        ),
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

    use_default_fetcher = fetcher is None
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
                text = _fetch_source_text(
                    source,
                    fetcher=fetcher,
                    use_github_graphql=use_default_fetcher,
                )
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
            if source.kind != "github-discussions":
                summaries.extend(
                    _summaries_for_interval(
                        text,
                        from_version=from_version,
                        to_version=to_version,
                    )
                )
            for linked_url in _release_note_links_for_version(source.url, text, to_version):
                if linked_url in checked_urls:
                    continue
                checked_urls.append(linked_url)
                label = f"{source.label} matching release note"
                try:
                    linked_text = fetcher(linked_url)
                except Exception as exc:
                    failures.append(f"{label}: {exc}")
                    source_results.append(
                        SourceRef(
                            kind="changelog",
                            label=label,
                            url=linked_url,
                            version=to_version,
                            available=False,
                            note=str(exc),
                        )
                    )
                    continue
                source_results.append(
                    SourceRef(
                        kind="changelog",
                        label=label,
                        url=linked_url,
                        version=to_version,
                        available=True,
                    )
                )
                summaries.extend(
                    _summaries_for_interval(
                        linked_text,
                        from_version=from_version,
                        to_version=to_version,
                    )
                )

        if summaries:
            status = "found"
            unavailable_reason = ""
        elif checked_urls and len(failures) < len(checked_urls):
            status = "no-matching-version"
            summaries.append(
                "Official sources were fetched, but no package-version-specific "
                f"entry for {to_version} was found."
            )
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
        raw = response.read()
    if raw.startswith(b"\x1f\x8b"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def _fetch_source_text(
    source: SourceRef,
    *,
    fetcher: ReleaseNoteFetcher,
    use_github_graphql: bool,
) -> str:
    if use_github_graphql and source.kind == "github-discussions" and source.url:
        try:
            return _fetch_github_discussions_index(source.url)
        except Exception:
            pass
    if not source.url:
        return ""
    return fetcher(source.url)


def _fetch_github_discussions_index(url: str) -> str:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN or GH_TOKEN is required for GitHub Discussions GraphQL")
    owner, repo, category_slug = _parse_github_discussion_category_url(url)
    query = """
    query($owner: String!, $repo: String!) {
      repository(owner: $owner, name: $repo) {
        discussions(first: 50, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes {
            number
            title
            url
            body
            category {
              slug
            }
          }
        }
      }
    }
    """
    payload = json.dumps({"query": query, "variables": {"owner": owner, "repo": repo}}).encode()
    request = urllib.request.Request(
        "https://api.github.com/graphql",
        data=payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "agent-runtime-kit-sdk-evolution",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        response_payload = json.loads(response.read().decode("utf-8", errors="replace"))
    if response_payload.get("errors"):
        raise RuntimeError(str(response_payload["errors"]))
    return _format_github_discussions_index(response_payload, category_slug=category_slug)


def _parse_github_discussion_category_url(url: str) -> tuple[str, str, str]:
    parsed = urllib.parse.urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if (
        parsed.netloc != "github.com"
        or len(parts) < 5
        or parts[2] != "discussions"
        or parts[3] != "categories"
    ):
        raise ValueError(f"not a GitHub discussion category URL: {url}")
    return parts[0], parts[1], parts[4]


def _format_github_discussions_index(payload: Mapping[str, object], *, category_slug: str) -> str:
    repository = _mapping_or_empty(_mapping_or_empty(payload.get("data")).get("repository"))
    discussions = _mapping_or_empty(repository.get("discussions"))
    lines: list[str] = []
    for item in discussions.get("nodes") or ():
        if not isinstance(item, Mapping):
            continue
        category = _mapping_or_empty(item.get("category"))
        if category_slug and category.get("slug") != category_slug:
            continue
        title = str(item.get("title") or "")
        url = str(item.get("url") or "")
        body = str(item.get("body") or "")
        if url:
            lines.append(f'<a href="{url}">{title}</a>')
        else:
            lines.append(title)
        if body:
            lines.append(body)
    return "\n".join(lines)


def _summaries_for_interval(
    text: str,
    *,
    from_version: str | None,
    to_version: str,
) -> list[str]:
    text = _prefer_github_discussion_body(text)
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
            for nearby in lines[index + 1 : index + 9]:
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


def _prefer_github_discussion_body(text: str) -> str:
    match = re.search(
        r"<td\b(?=[^>]*comment-body)(?=[^>]*markdown-body)[^>]*>(?P<body>.*?)</td>",
        text,
        flags=re.DOTALL,
    )
    if not match:
        return text
    return match.group("body")


def _release_note_links_for_version(source_url: str, text: str, to_version: str) -> tuple[str, ...]:
    if "/discussions/categories/" not in source_url:
        return ()
    markers = (to_version, f"v{to_version}")
    links: list[str] = []
    link_pattern = r'href="(?P<path>(?:https://github\.com)?/[^"]+/discussions/\d+)(?:[^"]*)?"'
    for match in re.finditer(link_pattern, text):
        window = text[match.end() : match.end() + 1200]
        if not any(marker in window for marker in markers):
            continue
        links.append(urllib.parse.urljoin(source_url, match.group("path")))
    return tuple(_dedupe(links))


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


def _mapping_or_empty(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    return {}


def _string_or_none(value: object) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None
