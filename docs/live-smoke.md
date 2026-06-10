# Live Smoke Tests

Default tests never call live providers. Live smoke tests are skipped unless
you opt in explicitly:

```bash
AGENT_RUNTIME_KIT_LIVE=1 AGENT_RUNTIME_KIT_LIVE_PROVIDER=claude uv run pytest -m live
```

Provider values:

- `claude`
- `codex`
- `antigravity`
- `all`

Expected setup:

- Claude: install `agent-runtime-kit[claude]` and configure Claude Agent SDK
  auth according to vendor docs.
- Codex: install `agent-runtime-kit[codex]` and configure local Codex auth/app
  server according to vendor docs.
- Antigravity: install `agent-runtime-kit[antigravity]` and set
  `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

The smoke prompt is intentionally small. It verifies adapter construction,
availability diagnostics, one task invocation, and a non-empty result.
