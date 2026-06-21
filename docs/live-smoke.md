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
  auth according to vendor docs. Supported provider modes include Anthropic API
  key auth, Amazon Bedrock via `CLAUDE_CODE_USE_BEDROCK=1` and the AWS SDK
  credential chain, Google Vertex AI, Claude Platform on AWS, and Azure AI
  Foundry.
- Codex: install `agent-runtime-kit[codex]` and configure local Codex auth/app
  server according to vendor docs. Codex can use ChatGPT sign-in, API-key
  sign-in, access-token setup, custom model providers, and the built-in
  `amazon-bedrock` model provider.
- Antigravity: install `agent-runtime-kit[antigravity]` and either set
  `GEMINI_API_KEY` / `GOOGLE_API_KEY` or configure Google Application Default
  Credentials for Vertex AI. When using ADC, provide a project through ADC or
  `GOOGLE_CLOUD_PROJECT`; the adapter defaults location to `global` unless
  `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_REGION`, or `CLOUD_ML_REGION` is set.

The smoke prompt is intentionally small. It verifies adapter construction,
availability diagnostics, one task invocation, and a non-empty result.
