"""Release-note evidence collection for SDK evolution runs."""

from __future__ import annotations

import ast
import gzip
import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence

from packaging.version import Version

from examples.sdk_evolution_agent.models import (
    ApiSnapshot,
    BundledRuntimeEvidence,
    BundledRuntimeRelease,
    ReleaseNoteEvidence,
    SourceRef,
)

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
            url="https://learn.chatgpt.com/docs/codex-sdk",
        ),
        SourceRef(
            kind="changelog",
            label="Codex changelog",
            url="https://learn.chatgpt.com/docs/changelog",
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
    snapshots: Sequence[ApiSnapshot] | None = None,
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
                    unavailable_reason="no newer compatible candidate",
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
            if use_default_fetcher:
                _progress(f"checking {name} release evidence: {source.label}")
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
                if use_default_fetcher:
                    _progress(f"checking {name} release evidence: {label}")
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

        bundled_runtime = None
        if name == "claude-agent-sdk" and snapshots is not None:
            bundled_runtime = _collect_claude_bundled_runtime(
                from_version, to_version, snapshots=snapshots, fetcher=fetcher
            )
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
                bundled_runtime=bundled_runtime,
            )
        )
    return tuple(evidence)


def _collect_claude_bundled_runtime(
    from_version: str | None,
    to_version: str,
    *,
    snapshots: Sequence[ApiSnapshot],
    fetcher: ReleaseNoteFetcher,
) -> BundledRuntimeEvidence:
    """Resolve CLI versions from exact SDK snapshots, then collect their releases.

    The upstream SDK tag is only accepted when its version file has
    the same fingerprint as the inspected distribution. A release index or tag
    that cannot be reconciled is evidence of a gap, not an empty changelog.
    """

    sources: list[SourceRef] = []
    versions: list[str] = []
    try:
        for sdk_version in (from_version, to_version):
            if not sdk_version or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]*", sdk_version):
                raise ValueError(f"invalid or missing Claude SDK version: {sdk_version!r}")
            snapshot = next(
                (
                    item
                    for item in snapshots
                    if item.package == "claude-agent-sdk"
                    and item.version == sdk_version
                    and not item.import_error
                ),
                None,
            )
            if snapshot is None:
                raise ValueError(f"missing exact SDK snapshot for {sdk_version}")
            version_file = next(
                (
                    item
                    for item in snapshot.implementation_files
                    if item.path == "claude_agent_sdk/_cli_version.py"
                ),
                None,
            )
            if version_file is None:
                raise ValueError(f"missing bundled CLI version fingerprint for SDK {sdk_version}")
            url = (
                "https://raw.githubusercontent.com/anthropics/claude-agent-sdk-python/"
                f"v{urllib.parse.quote(sdk_version, safe='')}/src/claude_agent_sdk/_cli_version.py"
            )
            _progress(f"checking bundled Claude CLI version from SDK {sdk_version}")
            try:
                content = fetcher(url)
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest != version_file.sha256:
                    raise ValueError(
                        f"bundled CLI version fingerprint mismatch for SDK {sdk_version}"
                    )
                cli_version = _claude_cli_version(content)
            except Exception as exc:
                sources.append(
                    SourceRef(
                        kind="sdk-runtime-version",
                        label="Claude SDK bundled CLI version",
                        url=url,
                        version=sdk_version,
                        available=False,
                        note=str(exc),
                    )
                )
                raise
            sources.append(
                SourceRef(
                    kind="sdk-runtime-version",
                    label="Claude SDK bundled CLI version",
                    url=url,
                    version=sdk_version,
                    note="Source fingerprint matches the inspected SDK distribution.",
                )
            )
            versions.append(cli_version)

        baseline, candidate = (Version(value) for value in versions)
        if candidate < baseline:
            raise ValueError("candidate bundled CLI version is older than the baseline")
        if candidate == baseline:
            return BundledRuntimeEvidence(
                "claude-code", versions[0], versions[1], "not-needed", tuple(sources)
            )

        releases: dict[Version, BundledRuntimeRelease] = {}
        covered_baseline = False
        for page in range(1, 6):
            url = (
                "https://api.github.com/repos/anthropics/claude-code/releases"
                f"?per_page=100&page={page}"
            )
            _progress(f"checking bundled Claude CLI release notes, page {page}")
            try:
                rows = json.loads(fetcher(url))
                if not isinstance(rows, list):
                    raise ValueError("release index is not a list")
            except Exception as exc:
                sources.append(
                    SourceRef(
                        kind="release-index",
                        label="Claude Code releases",
                        url=url,
                        available=False,
                        note=str(exc),
                    )
                )
                raise
            sources.append(SourceRef(kind="release-index", label="Claude Code releases", url=url))
            for row in rows:
                if not isinstance(row, Mapping):
                    raise ValueError("release index contains a malformed entry")
                if row.get("draft") or row.get("prerelease"):
                    continue
                tag = str(row.get("tag_name") or "")
                if not re.fullmatch(r"v?\d+\.\d+\.\d+", tag):
                    continue
                release_version = Version(tag.lstrip("v"))
                if release_version <= baseline:
                    covered_baseline = True
                if baseline < release_version <= candidate:
                    release_url = f"https://github.com/anthropics/claude-code/releases/tag/{tag}"
                    release = BundledRuntimeRelease(
                        version=str(release_version),
                        url=release_url,
                        notes=str(row.get("body") or "").strip(),
                    )
                    if release_version in releases and releases[release_version] != release:
                        raise ValueError(f"contradictory CLI releases for {release_version}")
                    releases[release_version] = release
            if covered_baseline or len(rows) < 100:
                covered_baseline = True
                break

        missing = [str(version) for version, release in releases.items() if not release.notes]
        problems = []
        if not covered_baseline:
            problems.append("release-index pagination did not reach the baseline")
        if candidate not in releases:
            problems.append(f"candidate CLI release {candidate} not found")
        if missing:
            problems.append("release notes missing for " + ", ".join(sorted(missing)))
        return BundledRuntimeEvidence(
            name="claude-code",
            from_version=versions[0],
            to_version=versions[1],
            status="incomplete" if problems else "found",
            sources=tuple(sources),
            releases=tuple(releases[key] for key in sorted(releases)),
            unavailable_reason="; ".join(problems),
        )
    except Exception as exc:
        return BundledRuntimeEvidence(
            name="claude-code",
            from_version=versions[0] if versions else None,
            to_version=versions[1] if len(versions) > 1 else None,
            status="unavailable",
            sources=tuple(sources),
            unavailable_reason=str(exc),
        )


def _claude_cli_version(content: str) -> str:
    tree = ast.parse(content)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__cli_version__"
            for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            if isinstance(value, str) and re.fullmatch(r"\d+\.\d+\.\d+", value):
                return value
    raise ValueError("SDK bundled CLI version is missing or unsupported")


def fetch_url_text(url: str) -> str:
    """Fetch a release-note source as text."""

    request = urllib.request.Request(url, headers={"User-Agent": "agent-runtime-kit-sdk-evolution"})
    raw = _urlopen_bytes(request)
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
        token = _github_token()
        if token:
            # With a token configured, a GraphQL failure (expired/insufficient
            # credentials, API errors) must surface — the caller records it on
            # the source — instead of silently downgrading to the
            # unauthenticated HTML scrape, which would hide a broken token
            # behind lower-quality evidence. Without a token, the HTML path is
            # simply the tokenless mode of operation.
            return _fetch_github_discussions_index(source.url, token=token)
    if not source.url:
        return ""
    return fetcher(source.url)


def _github_token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _fetch_github_discussions_index(url: str, *, token: str) -> str:
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
    response_payload = json.loads(_urlopen_bytes(request).decode("utf-8", errors="replace"))
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
    versions = [to_version]
    if from_version:
        versions.append(from_version)
    version_patterns = [
        re.compile(rf"(?<![\w.+-])v?{re.escape(version)}(?![\w.+-])") for version in versions
    ]
    # A baseline mention or a longer version sharing the candidate's prefix is
    # not release evidence for the candidate itself.
    if not version_patterns[0].search(text):
        return []
    matches: list[str] = []
    for index, line in enumerate(lines):
        if not line:
            continue
        if any(pattern.search(line) for pattern in version_patterns):
            matches.append(_clean_summary(line))
            for nearby in lines[index + 1 : index + 9]:
                cleaned = _clean_summary(nearby)
                if cleaned:
                    matches.append(cleaned)
    if not matches and to_version:
        compact = re.sub(r"\s+", " ", text)
        version_match = version_patterns[0].search(compact)
        if version_match:
            version_index = version_match.start()
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
    # GitHub release discussions may identify the version only in the page
    # title, while the comment body describes the changes without repeating it.
    # Keep that title attached to its body, excluding navigation and replies.
    title = re.search(r"<title\b[^>]*>(.*?)</title>", text, flags=re.DOTALL | re.IGNORECASE)
    body = match.group("body")
    return f"{title.group(1)}\n{body}" if title else body


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
        resolved = urllib.parse.urljoin(source_url, match.group("path"))
        parsed = urllib.parse.urlparse(resolved)
        # These hrefs come from fetched content that can carry user-generated
        # markup (discussion bodies). Only follow https URLs on github.com
        # itself: a protocol-relative path ("//evil.example/x/discussions/1")
        # would otherwise urljoin into an off-site request.
        if parsed.scheme != "https" or parsed.netloc != "github.com":
            continue
        links.append(resolved)
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


def _urlopen_bytes(request: urllib.request.Request) -> bytes:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.25 * (attempt + 1))
    raise AssertionError("unreachable")


def _progress(message: str) -> None:
    print(f"[sdk-evolution] {message}", flush=True)
