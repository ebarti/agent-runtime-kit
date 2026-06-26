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

- Status: `changed`
- Changed contracts: `2`
- Breaking contracts: `0`
- Diff count: `2`

## Direction Of Travel

```json
{
  "packages": [
    {
      "direction": "Advance lock from 0.2.106 to 0.2.110; no adapter code direction indicated by provided evidence.",
      "evidence": [
        "API diff reports no added, changed, or removed symbols for 0.2.106 -> 0.2.110.",
        "Candidate 0.2.110 adapter-contract probe passes: ClaudeAgentOptions exposes all required adapter fields.",
        "Current-environment 0.2.106 failure is import availability: No module named 'claude_agent_sdk'.",
        "Dry-run resolver selects 0.2.110 and completes successfully."
      ],
      "name": "claude-agent-sdk"
    },
    {
      "direction": "Advance lock from 0.1.4 to 0.1.5, but treat as adapter-watch because config classes changed.",
      "evidence": [
        "API diff marks AgentConfig and LocalAgentConfig as changed for 0.1.4 -> 0.1.5.",
        "Candidate 0.1.5 adapter-contract probe passes: LocalAgentConfig exposes all required adapter fields.",
        "Current-environment 0.1.4 failure is import availability: No module named 'google'.",
        "Dry-run resolver selects 0.1.5 and completes successfully."
      ],
      "name": "google-antigravity"
    },
    {
      "direction": "Hold at 0.1.0b3; no resolver-selected update or API action indicated.",
      "evidence": [
        "Installed, locked, and latest versions are all 0.1.0b3.",
        "Adapter-contract probe passes for required thread start and run parameters.",
        "Release-note status is not-needed because there is no resolver-selected update."
      ],
      "name": "openai-codex"
    },
    {
      "direction": "Keep current locked 0.137.0a4 unless a policy requires stable-only binaries; no resolver-selected update indicated.",
      "evidence": [
        "Installed and locked version is 0.137.0a4.",
        "Binary-distribution probe passes with metadata available.",
        "Resolver dry run does not select a Codex CLI binary change.",
        "Package metadata latest is reported as 0.136.0 while recent versions include 0.137.0a4."
      ],
      "name": "openai-codex-cli-bin"
    }
  ],
  "themes": [
    {
      "name": "Upgrade Scope",
      "summary": "Only claude-agent-sdk and google-antigravity are selected by the lock dry run; Codex packages remain unchanged."
    },
    {
      "name": "Contract Risk",
      "summary": "Both selected candidates pass adapter-contract probes, and no required adapter fields are missing."
    },
    {
      "name": "Vendor Specificity",
      "summary": "Antigravity deserves closer review than Claude because its config classes changed even though required fields remain present."
    },
    {
      "name": "Environment Signal",
      "summary": "Current-environment failures for Claude and Antigravity are missing-module failures, not evidence of runtime behavior regressions."
    },
    {
      "name": "Release Notes",
      "summary": "Claude notes show internal/other changes; Antigravity notes were found but extracted summaries are mostly metadata-like rather than semantic."
    }
  ],
  "uncertainty": [
    "The evidence does not include semantic details for Antigravity LocalAgentConfig changes beyond required-field presence.",
    "Candidate probes validate adapter surface shape, not live task execution, authentication, streaming, or vendor runtime behavior.",
    "Claude and Antigravity are not importable in the current environment, so local installed-package behavior is not directly observed.",
    "openai-codex-cli-bin has a locked prerelease newer than reported latest stable metadata; direction depends on project prerelease policy."
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
      "classification": "compatible-update",
      "evidence": [
        "API diff reports no added, changed, or removed symbols.",
        "Candidate adapter-contract probe passes with all required ClaudeAgentOptions fields present.",
        "Release-note evidence status is found.",
        "Dry-run resolver selects 0.2.110 successfully."
      ],
      "summary": "Claude can advance from 0.2.106 to 0.2.110 without adapter design changes indicated."
    },
    {
      "classification": "compatible-update-with-watch",
      "evidence": [
        "API diff marks AgentConfig and LocalAgentConfig as changed.",
        "Candidate adapter-contract probe passes with all required LocalAgentConfig fields present.",
        "Release-note evidence status is found.",
        "Dry-run resolver selects 0.1.5 successfully."
      ],
      "summary": "Antigravity can advance from 0.1.4 to 0.1.5, with config-class changes worth watching but not blocking."
    },
    {
      "classification": "no-action",
      "evidence": [
        "Installed, locked, and latest versions are all 0.1.0b3.",
        "Current-environment adapter-contract probe passes for required thread start and run parameters.",
        "Release notes are not needed because no resolver-selected update exists."
      ],
      "summary": "OpenAI Codex SDK remains at 0.1.0b3 with its adapter contract intact."
    },
    {
      "classification": "no-action-policy-dependent",
      "evidence": [
        "Installed and locked version is 0.137.0a4.",
        "Binary-distribution probe passes with metadata available.",
        "Resolver dry run does not select a CLI binary change.",
        "Reported latest metadata is 0.136.0 while recent versions include 0.137.0a4."
      ],
      "summary": "Codex CLI binary remains at 0.137.0a4 unless project policy rejects prerelease binary locks."
    }
  ],
  "manual_design_required": false,
  "recursive_self_adaptation_impact": false,
  "safe_to_implement": true,
  "self_adaptation_plan": [
    "Update lock state for claude-agent-sdk to 0.2.110.",
    "Update lock state for google-antigravity to 0.1.5.",
    "Do not change adapter source based on the provided evidence.",
    "Keep Codex packages unchanged.",
    "Track Antigravity config-class semantics in verification because only required-field presence was proven."
  ],
  "uncertainty": [
    "Antigravity LocalAgentConfig semantic changes are not described beyond required-field presence.",
    "Candidate probes do not prove live task execution, authentication, streaming, or vendor runtime behavior.",
    "Claude and Antigravity were not importable in the current environment before candidate probing.",
    "Codex CLI prerelease lock policy is not specified."
  ],
  "verification_commands": [
    "uv lock -P claude-agent-sdk -P google-antigravity",
    "uv lock --dry-run -P claude-agent-sdk -P openai-codex -P openai-codex-cli-bin -P google-antigravity",
    "uv run pytest"
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
    "Update lock state for claude-agent-sdk to 0.2.110.",
    "Update lock state for google-antigravity to 0.1.5.",
    "Do not change adapter source based on the provided evidence.",
    "Keep Codex packages unchanged.",
    "Track Antigravity config-class semantics in verification because only required-field presence was proven."
  ],
  "verification_results": [
    {
      "command": [
        "git",
        "switch",
        "-c",
        "sdk-evolution-module-proof-20260626-151158"
      ],
      "removed_env": [],
      "returncode": 0,
      "stderr": "Switched to a new branch 'sdk-evolution-module-proof-20260626-151158'\n",
      "stdout": ""
    },
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
      "stderr": "Resolved 69 packages in 97ms\nUpdated claude-agent-sdk v0.2.106 -> v0.2.110\nUpdated google-antigravity v0.1.4 -> v0.1.5\n",
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
      "stdout": "Success: no issues found in 14 source files\n"
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
      "stdout": "============================= test session starts ==============================\nplatform darwin -- Python 3.10.13, pytest-9.0.3, pluggy-1.6.0\nrootdir: /private/tmp/sdk-evolution-module-proof-20260626-151158\nconfigfile: pyproject.toml\nplugins: asyncio-1.4.0\nasyncio: mode=strict, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function\ncollected 127 items\n\ntests/test_antigravity_adapter.py ..................                     [ 14%]\ntests/test_claude_adapter.py .................                           [ 27%]\ntests/test_codex_adapter.py .......................                      [ 45%]\ntests/test_core.py ....                                                  [ 48%]\ntests/test_events.py .....                                               [ 52%]\ntests/test_live_smoke.py sss                                             [ 55%]\ntests/test_mestre_compatibility.py ..                                    [ 56%]\ntests/test_optional_dependencies.py .                                    [ 57%]\ntests/test_provider_diagnostics.py .                                     [ 58%]\ntests/test_sdk_contract.py ss....sss                                     [ 65%]\ntests/test_sdk_evolution_agent.py ...................................... [ 95%]\n.                                                                        [ 96%]\ntests/test_sdk_evolution_upgrade_script.py .....                         [100%]\n\n======================== 119 passed, 8 skipped in 0.51s ========================\n"
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
    "Candidate API diffs are present for the selected Claude and Antigravity updates.",
    "Claude 0.2.110 exposes all required adapter fields and shows no public API symbol drift.",
    "Antigravity 0.1.5 changes config classes, but the candidate contract probe shows all required LocalAgentConfig fields present.",
    "No behavior diff is marked breaking; current-environment failures are missing-module availability signals, not contract regressions.",
    "Release-note evidence is found for selected updates; Codex packages have no resolver-selected update."
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
