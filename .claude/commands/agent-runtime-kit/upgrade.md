---
name: "Agent Runtime Kit: Upgrade"
description: Inspect and safely upgrade agent-runtime-kit vendor SDK dependencies
category: Workflow
tags: [agent-runtime-kit, sdk-evolution, upgrade, workflow]
---

# Agent Runtime Kit SDK Upgrade

Read and follow the canonical repository workflow in
`.codex/skills/agent-runtime-kit-upgrade/SKILL.md`. That file owns candidate
discovery, no-cooloff resolution, report-first gates, local upgrade
authorization, verification, and PR publication rules; do not duplicate or
weaken them here.

For this Claude command, use `claude-agent-sdk` with `--extra claude` unless the
user selected another runtime. Preserve every other authorization boundary from
the canonical workflow: an explicit SDK upgrade request permits the gated local
dependency update, while branch, commit, push, PR, merge, and release actions
remain separately authorized.
