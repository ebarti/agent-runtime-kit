# Publish Checklist

`agent-runtime-kit` is not published yet.

## Current Name Check

Checked on 2026-06-10 from this workspace:

```bash
python - <<'PY'
import urllib.error, urllib.request
try:
    urllib.request.urlopen("https://pypi.org/pypi/agent-runtime-kit/json", timeout=10)
    print("TAKEN")
except urllib.error.HTTPError as exc:
    print("FREE" if exc.code == 404 else f"HTTP_{exc.code}")
PY
```

Result: `FREE`.

Re-run this immediately before publishing. A 404 means the name is still
available; any 200 response means it has been claimed.

## Release Gate

- `uv run pytest`
- `uv run ruff check .`
- `uv run mypy`
- `uv run python -m build`
- Optional: provider-specific live smoke tests from `docs/live-smoke.md`

## Metadata Gate

- Confirm `pyproject.toml` package name is `agent-runtime-kit`.
- Confirm Python support remains `>=3.10`.
- Confirm optional extras resolve for `claude`, `codex`, `antigravity`, and
  `all`.
- Confirm README links render on PyPI.
- Confirm `LICENSE` is included.

## Publish

```bash
uv publish
```

Do not publish until the release gate passes and the PyPI name check is fresh.
