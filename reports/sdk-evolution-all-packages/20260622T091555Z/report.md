# SDK Evolution Agent Report

## Run

- Runtime: `antigravity-agent-sdk`
- Implementation enabled: `False`
- Draft PR enabled: `False`

## Upstream Evidence

- claude-agent-sdk: locked=0.2.96 installed=0.2.96 latest=0.2.106
- openai-codex: locked=0.1.0b3 installed=0.1.0b3 latest=0.1.0b3
- openai-codex-cli-bin: locked=0.137.0a4 installed=0.137.0a4 latest=0.136.0
- google-antigravity: locked=0.1.2 installed=0.1.2 latest=0.1.4

## API Diffs

- Diff count: `2`

## Direction Of Travel

```json
{
  "packages": [
    {
      "direction": "Integrate new task tracking status updates and terminal task status features into the adapter.",
      "evidence": [
        "Added TERMINAL_TASK_STATUSES, TaskUpdatedMessage, TaskUpdatedStatus in claude-agent-sdk v0.2.106."
      ],
      "name": "claude-agent-sdk"
    },
    {
      "direction": "Refactor the adapter to use the new ModelTarget, ModelType, and Endpoint APIs while transitioning away from legacy ModelConfig and GeminiConfig.",
      "evidence": [
        "Removed GeminiConfig, GenerationConfig, ModelConfig, ModelEntry, and mcp.",
        "Added ModelTarget, ModelType, GeminiAPIEndpoint, VertexEndpoint, GeminiModelOptions, SystemInstructions, Audio, Video, Image, Document, Content.",
        "Changed CapabilitiesConfig, LocalAgentConfig, ToolContext."
      ],
      "name": "google-antigravity"
    }
  ],
  "themes": [
    {
      "name": "Configuration Modernization",
      "summary": "Transition from generic config structures (GeminiConfig, ModelConfig) to structured and typed model/endpoint definitions (ModelTarget, GeminiAPIEndpoint, VertexEndpoint, and GeminiModelOptions)."
    },
    {
      "name": "Advanced Task State Tracking",
      "summary": "Addition of task and status updates (TaskUpdatedMessage, TaskUpdatedStatus, TERMINAL_TASK_STATUSES) allowing more robust asynchronous tracking of agent progress."
    }
  ],
  "uncertainty": [
    "The exact schema and behavior changes in CapabilitiesConfig, LocalAgentConfig, and ToolContext.",
    "The specific required migration path for the now removed mcp module or configuration."
  ]
}
```

## Architecture Decision

- Manual design required: `True`
- Recursive self-adaptation impact: `False`
- Safe to implement: `False`

```json
{
  "findings": [
    {
      "classification": "breaking_change",
      "evidence": [
        "google-antigravity removed: GeminiConfig, GenerationConfig, ModelConfig, ModelEntry, mcp",
        "google-antigravity added: ModelTarget, ModelType, GeminiAPIEndpoint, GeminiModelOptions",
        "Changed CapabilitiesConfig, LocalAgentConfig, ToolContext"
      ],
      "summary": "The google-antigravity package removed key configuration components (GeminiConfig, GenerationConfig, ModelConfig, ModelEntry, mcp) and introduced a modernized ModelTarget and GeminiAPIEndpoint API, requiring a rewrite of adapter config parsing."
    },
    {
      "classification": "feature_enhancement",
      "evidence": [
        "claude-agent-sdk added: TERMINAL_TASK_STATUSES, TaskUpdatedMessage, TaskUpdatedStatus"
      ],
      "summary": "The claude-agent-sdk package introduced TERMINAL_TASK_STATUSES, TaskUpdatedMessage, and TaskUpdatedStatus for robust tracking of asynchronous task progression."
    }
  ],
  "manual_design_required": true,
  "recursive_self_adaptation_impact": false,
  "safe_to_implement": false,
  "self_adaptation_plan": [
    "Design a new configuration schema mapping LocalAgentConfig and CapabilitiesConfig to the new ModelTarget and GeminiAPIEndpoint structures.",
    "Identify if the removed 'mcp' functionalities are replaced by 'BuiltinTools' or configured differently.",
    "Update the Claude adapter to subscribe to TaskUpdatedMessage and handle terminal task statuses.",
    "Implement unit tests to verify the compatibility of upgraded config schemas and tracking events."
  ],
  "uncertainty": [
    "No code access to the internal structures of CapabilitiesConfig, LocalAgentConfig, or ToolContext changes in google-antigravity v0.1.4.",
    "The specific migration path or alternative configuration options for the removed 'mcp' module.",
    "Whether terminal task statuses are automatically handled or require explicit event loop registration in the Claude adapter."
  ],
  "verification_commands": [
    "pytest tests/unit/adapters/test_antigravity.py",
    "pytest tests/unit/adapters/test_claude.py"
  ]
}
```

## Implementation Summary

```json
{
  "applied": false,
  "blocked_reason": "report-only mode",
  "changes": [],
  "verification_results": []
}
```

## Reviewer Output

```json
{
  "reasons": [
    "google-antigravity v0.1.4 introduces breaking changes by removing GeminiConfig, GenerationConfig, ModelConfig, ModelEntry, and mcp.",
    "The CapabilitiesConfig, LocalAgentConfig, and ToolContext configurations have changed with no direct visibility into their new internal structures.",
    "The removal of the mcp module requires a manual migration or replacement alignment.",
    "Integrated task tracking additions (TaskUpdatedMessage) in claude-agent-sdk v0.2.106 require manual adapter code modifications."
  ],
  "required_changes": [
    "Design and implement a new config schema mapping LocalAgentConfig and CapabilitiesConfig to the new ModelTarget and GeminiAPIEndpoint structures.",
    "Determine if removed mcp functionalities should be replaced by BuiltinTools or another mechanism.",
    "Refactor the claude adapter to consume TaskUpdatedMessage and handle terminal task statuses.",
    "Verify the compatibility of upgraded config schemas and tracking events through comprehensive unit testing."
  ],
  "status": "rejected"
}
```

## Manual Review Checklist

- Verify source references are enough for every architecture finding.
- Verify vendor-specific behavior has not been flattened.
- Verify recursive self-adaptation impact is handled or explicitly blocked.
- Verify tests, docs, examples, and migration notes match public API changes.
- Confirm no auto-merge or unsupported credential scraping was used.
