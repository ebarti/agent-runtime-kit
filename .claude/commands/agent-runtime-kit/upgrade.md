---
name: "Agent Runtime Kit: Upgrade"
description: Run the checked-in agent-runtime-kit SDK evolution upgrade script for all tracked upstream SDK packages.
category: Workflow
tags: [agent-runtime-kit, sdk-evolution, upgrade, workflow]
---

Run the checked-in script. Do not recreate the workflow as copied shell
commands, and do not pass a package subset; the script always targets all tracked
SDK packages:

- `claude-agent-sdk`
- `openai-codex`
- `openai-codex-cli-bin`
- `google-antigravity`

Default Claude-command invocation:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python scripts/sdk_evolution_upgrade.py --runtime claude-agent-sdk
```

For a report-only evidence/decision pass:

```bash
env -u UV_EXCLUDE_NEWER -u UV_EXCLUDE_NEWER_PACKAGE \
  uv run python scripts/sdk_evolution_upgrade.py --runtime claude-agent-sdk --report-only
```

The script creates a collision-free branch and worktree, prepares Codex auth
when `--runtime codex-agent-sdk` is selected, runs the report-only gate first,
then runs the implementation/draft-PR pass unless `--report-only` is set. It
never auto-merges.
