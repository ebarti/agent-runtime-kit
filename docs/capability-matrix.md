# Capability Matrix

| Capability | Claude Agent SDK | OpenAI Codex SDK | Google Antigravity SDK |
|------------|------------------|------------------|------------------------|
| Optional extra | `claude` | `codex` | `antigravity` |
| Core import without extra | Yes | Yes | Yes |
| Working directory | Yes | Yes | Yes |
| Session resume | Yes | Yes | Yes |
| Structured output | Native `output_format` when available | Native output schema / JSON parse fallback | Native response schema / JSON parse fallback |
| MCP stdio servers | Yes | No per-task MCP config | Yes, without per-server env |
| Permission mapping | `permission_mode` | approval mode + sandbox | capabilities + policies |
| Streaming output events | From streamed SDK messages | Not enabled in v1 adapter | From response chunks |
| Tool audit events | Best effort from message content | Best effort from result surface | From tool chunks |
| Missing package diagnostics | Yes | Yes | Yes |
| Missing credential diagnostics | Provider-owned/local auth | Provider-owned/local auth | `GEMINI_API_KEY` or `GOOGLE_API_KEY` |
| Live smoke test | Opt-in | Opt-in | Opt-in |

The matrix is intentionally not a lowest-common-denominator contract. Adapters
reject unsupported inputs when silently dropping them would be misleading.
