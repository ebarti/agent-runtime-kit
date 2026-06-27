# SDK Evolution Agent Report

## Run

- Runtime: `codex-agent-sdk`
- Implementation enabled: `True`
- Draft PR enabled: `True`

## Upstream Evidence

- claude-agent-sdk: locked=0.2.106 installed=None latest=0.2.110
- openai-codex: locked=0.1.0b3 installed=0.1.0b3 latest=0.1.0b3
- openai-codex-cli-bin: locked=0.137.0a4 installed=0.137.0a4 latest=0.136.0
- google-antigravity: locked=0.1.4 installed=None latest=0.1.5

## API Diffs

- Diff count: `2`

## Release Notes

- claude-agent-sdk: found (0.2.106 -> 0.2.110)
- google-antigravity: found (0.1.4 -> 0.1.5)

## Behavior Probes

- Status: `pass`
- Changed contracts: `0`
- Breaking contracts: `0`
- Diff count: `2`

## Direction Of Travel

```json
{
  "packages": [
    {
      "direction": "Proceed with the resolver-selected refresh from 0.2.106 to 0.2.110; no adapter code change is indicated by the evidence.",
      "evidence": [
        "API diff shows no added, removed, or changed symbols between 0.2.106 and 0.2.110.",
        "Adapter-contract probe passed before and after; required ClaudeAgentOptions fields remain present.",
        "Release notes found for 0.2.110, summarized only as Internal/Other Changes."
      ],
      "name": "claude-agent-sdk"
    },
    {
      "direction": "Proceed cautiously with the refresh from 0.1.4 to 0.1.5; preserve current adapter behavior and review the new LocalAgentConfig surface separately.",
      "evidence": [
        "API diff marks AgentConfig and LocalAgentConfig as changed between 0.1.4 and 0.1.5.",
        "Candidate LocalAgentConfig still exposes all required adapter fields and adds subagents.",
        "Adapter-contract probe passed before and after with no behavior difference detected."
      ],
      "name": "google-antigravity"
    },
    {
      "direction": "Keep current 0.1.0b3 state; no resolver-selected update is present.",
      "evidence": [
        "Installed, locked, and latest versions are all 0.1.0b3.",
        "Codex thread API contract probe passes with required start and run parameters present.",
        "Release-note check was marked not-needed because no update was selected."
      ],
      "name": "openai-codex"
    },
    {
      "direction": "Keep the current locked binary runtime state unless prerelease policy is reviewed; no refresh action is indicated by the resolver preview.",
      "evidence": [
        "Installed and locked version is 0.137.0a4 while package metadata latest is 0.136.0.",
        "Binary-distribution metadata probe passed for 0.137.0a4.",
        "Release-note check was marked not-needed because no resolver-selected update occurred."
      ],
      "name": "openai-codex-cli-bin"
    }
  ],
  "themes": [
    {
      "name": "Low Contract Risk",
      "summary": "All adapter-contract probes pass before and after the selected updates, and behavior summary reports zero breaking changes."
    },
    {
      "name": "Surface Drift Is Isolated",
      "summary": "Only google-antigravity shows changed API classes, and the observed required adapter fields remain available."
    },
    {
      "name": "Lock Refresh Scope",
      "summary": "The dry-run resolver selects only claude-agent-sdk and google-antigravity updates; Codex packages remain unchanged."
    },
    {
      "name": "Optional Capability Growth",
      "summary": "Antigravity 0.1.5 adds subagents to LocalAgentConfig, which may be a future vendor-specific capability rather than a current compatibility requirement."
    }
  ],
  "uncertainty": [
    "Behavior probes are adapter-contract checks, not full live agent execution across vendor runtimes.",
    "Antigravity official sources were fetched, but no package-version-specific 0.1.5 release-note entry was found.",
    "Claude 0.2.110 release-note evidence is high level and does not enumerate detailed internal changes.",
    "claude-agent-sdk and google-antigravity installed_version values are null in the provided package evidence, so conclusions rely on lock, candidate, and probe data.",
    "openai-codex-cli-bin is locked to a prerelease-like 0.137.0a4 while metadata latest is 0.136.0; the evidence does not explain the version ordering policy."
  ]
}
```

## Architecture Decision

- Manual design required: `False`
- Recursive self-adaptation impact: `False`
- Safe to implement: `True`

```json
{
  "findings": [
    {
      "classification": "compatible-refresh",
      "evidence": [
        "API diff shows no added, removed, or changed symbols.",
        "Adapter-contract probe passed before and after with required ClaudeAgentOptions fields present.",
        "Release-note evidence status is found for 0.2.110."
      ],
      "summary": "Claude refresh from 0.2.106 to 0.2.110 is safe at the adapter-contract level."
    },
    {
      "classification": "compatible-surface-drift",
      "evidence": [
        "API diff marks AgentConfig and LocalAgentConfig as changed.",
        "Candidate LocalAgentConfig still exposes all required adapter fields.",
        "Adapter-contract probe passed before and after; candidate adds subagents as optional surface growth."
      ],
      "summary": "Google Antigravity refresh from 0.1.4 to 0.1.5 shows API surface drift but no required adapter-contract break."
    },
    {
      "classification": "unchanged-dependency",
      "evidence": [
        "Installed, locked, and latest openai-codex versions are 0.1.0b3.",
        "Codex thread API contract probe passes with required start and run parameters.",
        "Release-note status is not-needed because no resolver-selected update occurred."
      ],
      "summary": "OpenAI Codex SDK remains unchanged and does not require adapter action in this decision."
    },
    {
      "classification": "unchanged-prerelease-binary",
      "evidence": [
        "Installed and locked version is 0.137.0a4 while metadata latest is 0.136.0.",
        "Binary-distribution metadata probe passed for 0.137.0a4.",
        "No resolver-selected update occurred for the binary package."
      ],
      "summary": "openai-codex-cli-bin should remain at the locked 0.137.0a4 unless prerelease policy is reviewed separately."
    }
  ],
  "manual_design_required": false,
  "recursive_self_adaptation_impact": false,
  "safe_to_implement": true,
  "self_adaptation_plan": [
    "Refresh only resolver-selected packages: claude-agent-sdk 0.2.106 to 0.2.110 and google-antigravity 0.1.4 to 0.1.5.",
    "Preserve current adapter behavior; no adapter code change is indicated by the provided probes.",
    "Do not adopt Antigravity subagents in this change; treat it as a future vendor-specific capability review.",
    "Leave openai-codex and openai-codex-cli-bin unchanged."
  ],
  "uncertainty": [
    "Behavior probes validate adapter contracts, not full live vendor agent execution.",
    "Antigravity 0.1.5 release-note sources were found, but no package-version-specific entry was identified.",
    "Claude 0.2.110 release notes are high level and summarized as Internal/Other Changes.",
    "claude-agent-sdk and google-antigravity installed_version values are null in package evidence.",
    "openai-codex-cli-bin prerelease ordering policy is not explained by the evidence."
  ],
  "verification_commands": [
    "uv lock --dry-run -P claude-agent-sdk -P openai-codex -P openai-codex-cli-bin -P google-antigravity"
  ]
}
```

## Implementation Summary

```json
{
  "allowed": true,
  "applied": true,
  "blocked_reason": "",
  "changes": [
    "Updated uv.lock for resolver-selected SDK packages: claude-agent-sdk, google-antigravity"
  ],
  "planned_changes": [
    "Refresh only resolver-selected packages: claude-agent-sdk 0.2.106 to 0.2.110 and google-antigravity 0.1.4 to 0.1.5.",
    "Preserve current adapter behavior; no adapter code change is indicated by the provided probes.",
    "Do not adopt Antigravity subagents in this change; treat it as a future vendor-specific capability review.",
    "Leave openai-codex and openai-codex-cli-bin unchanged."
  ],
  "verification_results": [
    {
      "command": [
        "uv",
        "lock",
        "-P",
        "claude-agent-sdk",
        "-P",
        "google-antigravity"
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "Resolved 69 packages in 129ms\nUpdated claude-agent-sdk v0.2.106 -> v0.2.110\nUpdated google-antigravity v0.1.4 -> v0.1.5\n",
      "stdout": ""
    },
    {
      "command": [
        "uv",
        "run",
        "ruff",
        "check",
        "."
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "",
      "stdout": "All checks passed!\n"
    },
    {
      "command": [
        "uv",
        "run",
        "mypy"
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "",
      "stdout": "Success: no issues found in 15 source files\n"
    },
    {
      "command": [
        "uv",
        "run",
        "pytest"
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "",
      "stdout": "============================= test session starts ==============================\nplatform darwin -- Python 3.10.13, pytest-9.0.3, pluggy-1.6.0\nrootdir: /private/tmp/sdk-evolution-behavior-proof-20260627-232223-59ee6236\nconfigfile: pyproject.toml\nplugins: asyncio-1.4.0\nasyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function\ncollected 132 items\n\ntests/test_antigravity_adapter.py ..................                     [ 13%]\ntests/test_claude_adapter.py .................                           [ 26%]\ntests/test_codex_adapter.py .......................                      [ 43%]\ntests/test_core.py ....                                                  [ 46%]\ntests/test_events.py .....                                               [ 50%]\ntests/test_live_smoke.py sss                                             [ 53%]\ntests/test_mestre_compatibility.py ..                                    [ 54%]\ntests/test_optional_dependencies.py .                                    [ 55%]\ntests/test_provider_diagnostics.py .                                     [ 56%]\ntests/test_sdk_contract.py ss....sss                                     [ 62%]\ntests/test_sdk_evolution_agent.py ...................................... [ 91%]\n...                                                                      [ 93%]\ntests/test_sdk_evolution_upgrade_script.py ........                      [100%]\n\n======================== 124 passed, 8 skipped in 0.48s ========================\n"
    },
    {
      "command": [
        "uv",
        "lock",
        "--check"
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "Resolved 69 packages in 4ms\n",
      "stdout": ""
    }
  ]
}
```

## Current State Baseline

- Promotion status: `promoted`
- Promoted: `True`

## Reviewer Output

```json
{
  "reasons": [
    "Resolver-selected updates are limited to claude-agent-sdk 0.2.106->0.2.110 and google-antigravity 0.1.4->0.1.5.",
    "Adapter-contract behavior probes pass before and after for both selected updates with severity none and zero breaking changes.",
    "Claude API diff shows no symbol drift; Antigravity drift is limited to AgentConfig/LocalAgentConfig while required adapter fields remain present.",
    "Release-note evidence is found for both selected updates; Antigravity lacks package-version-specific text but is not unavailable under the gate policy.",
    "Uncertainty remains around live vendor execution and prerelease binary ordering, but no hard blocker is shown by the provided evidence."
  ],
  "required_changes": [],
  "status": "pass"
}
```

## Manual Review Checklist

- Verify source references are enough for every architecture finding.
- Verify vendor-specific behavior has not been flattened.
- Verify recursive self-adaptation impact is handled or explicitly blocked.
- Verify tests, docs, examples, and migration notes match public API changes.
- Confirm no auto-merge or unsupported credential scraping was used.
