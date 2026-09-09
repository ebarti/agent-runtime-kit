---
name: "Agent Runtime Kit: Upgrade"
description: Upgrade and adapt agent-runtime-kit vendor SDK integrations through a verified pull request
category: Workflow
tags: [agent-runtime-kit, sdk-evolution, upgrade, workflow]
---

# Agent Runtime Kit SDK Upgrade

Read and follow the canonical repository workflow in
`.codex/skills/agent-runtime-kit-upgrade/SKILL.md`. That file owns candidate
discovery, no-cooloff resolution, report-first gates, upstream adaptation,
verification, and PR publication rules; do not duplicate or
weaken them here.

For this Claude command, use `claude-agent-sdk` with `--extra claude` unless the
user selected another runtime. Complete the canonical workflow, including the
necessary source adaptations and regular PR, for an upgrade invocation. Honor
explicit report-only or local-only limits. Never auto-merge or publish a release.
