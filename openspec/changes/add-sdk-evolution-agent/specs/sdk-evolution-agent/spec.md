## ADDED Requirements

### Requirement: Local command entry point
The system SHALL provide a local SDK evolution agent command that can run from
the repository without requiring scheduled CI.

#### Scenario: Report-only local run
- **WHEN** a user runs `python -m examples.sdk_evolution_agent` with a configured agent-runtime-kit runtime
- **THEN** the command produces a local report for the inspected SDK set without creating a PR or merging changes

#### Scenario: Explicit runtime selection
- **WHEN** the user selects a Claude, Codex, Antigravity, or fake/test runtime supported by `RuntimeRegistry`
- **THEN** the command resolves that runtime through agent-runtime-kit primitives before any AI stage is executed

### Requirement: AI work uses agent-runtime-kit
The system MUST execute all AI-driven reasoning, planning, implementation
prompting, structured-output generation, and reviewer judgment through
agent-runtime-kit runtime primitives.

#### Scenario: AI direction analysis
- **WHEN** the agent asks an AI model to infer upstream SDK direction
- **THEN** the request is represented as an `AgentTask` executed through a runtime adapter resolved from `RuntimeRegistry`

#### Scenario: AI implementation prompt
- **WHEN** the agent asks an AI model to propose or apply code changes
- **THEN** the request uses `AgentTask`, `PermissionProfile`, `working_directory`, `output_schema`, `event_sink`, and a structured `AgentResult`

#### Scenario: Direct vendor model API is not used
- **WHEN** the agent needs AI reasoning or structured output
- **THEN** the implementation does not call OpenAI, Anthropic, Google, or other model APIs directly outside agent-runtime-kit

#### Scenario: Required runtime capability is unavailable
- **WHEN** the selected runtime cannot honor a required output schema, permission profile, working directory, or event sink behavior
- **THEN** the agent fails closed with a reportable unsupported-capability result instead of falling back to direct model calls

### Requirement: Fresh upstream version detection
The system SHALL detect newer available versions of `claude-agent-sdk`,
`openai-codex`, `openai-codex-cli-bin`, and `google-antigravity` against the
current local project state.

#### Scenario: PyPI freshness cutoff bypass
- **WHEN** the agent checks available upstream SDK versions or runs a targeted lockfile refresh preview
- **THEN** the command environment explicitly removes `UV_EXCLUDE_NEWER` and any configured freshness cutoff variables that would hide new SDK releases

#### Scenario: Targeted package refresh preview
- **WHEN** the agent previews SDK dependency updates
- **THEN** it uses a targeted package refresh for the vendor SDK packages instead of treating the workflow as a generic dependency update

#### Scenario: Current and candidate versions captured
- **WHEN** the agent completes version detection
- **THEN** the evidence bundle records locked versions, installed versions when discoverable, latest available versions, and selected recent versions inspected for direction analysis

### Requirement: Evidence bundle generation
The system SHALL generate a structured evidence bundle before any AI
architecture judgment is requested.

#### Scenario: Evidence sources captured
- **WHEN** upstream research runs
- **THEN** the evidence bundle includes source references for package metadata, release notes or changelogs when available, docs, examples, public module APIs, type signatures, and upstream repository history when available

#### Scenario: API snapshots are isolated
- **WHEN** the agent inspects candidate SDK versions
- **THEN** it captures public API snapshots and type/signature data in isolated temporary environments without mutating the project lockfile or installed working environment

#### Scenario: API diffs are produced
- **WHEN** current and candidate SDK snapshots are available
- **THEN** the agent produces structured API diffs tied back to source package versions and evidence references

### Requirement: Direction-of-travel analysis
The system SHALL produce structured direction-of-travel analysis for each
inspected upstream SDK.

#### Scenario: Themes are identified
- **WHEN** the AI analysis stage evaluates upstream evidence
- **THEN** it identifies relevant themes such as execution model, session/resume semantics, permissions, sandboxing, MCP/tool support, structured output, event streaming, authentication, workspace semantics, lifecycle, concurrency, deprecations, and breaking changes

#### Scenario: One-off drift is separated from product direction
- **WHEN** multiple versions or evidence sources are available
- **THEN** the analysis distinguishes isolated API drift from repeated direction and states the supporting evidence

#### Scenario: Uncertainty is explicit
- **WHEN** evidence is incomplete, ambiguous, or inferred from limited sources
- **THEN** the analysis records uncertainty and does not overstate the SDK direction

### Requirement: Architecture fit review
The system SHALL compare upstream direction against agent-runtime-kit's current
public API, adapter model, capability model, and error model.

#### Scenario: Finding classification
- **WHEN** an upstream change or direction is evaluated
- **THEN** the finding is classified as one of adapter-only, test-only, docs-only, capability metadata change, provider-specific extension, public API evolution, compatibility shim, deprecation/migration, architectural rework, or manual-design-required

#### Scenario: Vendor-specific behavior is preserved
- **WHEN** the architecture fit review proposes a change
- **THEN** it preserves provider-specific capabilities and constraints rather than flattening them into a lowest-common-denominator abstraction

#### Scenario: Unsupported behavior remains typed
- **WHEN** a runtime cannot support a requested SDK feature
- **THEN** the proposed design uses explicit capability metadata or typed unsupported-feature errors rather than silently dropping the behavior

### Requirement: Architecture decision precedes edits
The system MUST produce an architecture decision artifact before applying code,
test, docs, example, or compatibility changes.

#### Scenario: Safe local implementation approved
- **WHEN** the decision concludes that the evidence supports a high-confidence adapter, test, docs, capability metadata, provider-specific extension, or compatibility-shim change
- **THEN** the decision lists the exact implementation scope, verification commands, migration notes if needed, and evidence references before edits are made

#### Scenario: Manual design required
- **WHEN** the decision identifies architectural rework, unresolved public API evolution, broad provider semantics, or insufficient evidence
- **THEN** the agent sets `manual_design_required`, writes the report, and exits without implementation changes

### Requirement: Guarded implementation
The system SHALL apply local changes only when implementation is explicitly
enabled and all decision and review gates pass.

#### Scenario: Report-only default
- **WHEN** implementation is not explicitly enabled
- **THEN** the agent performs research, analysis, decision, review, and report generation without editing project code

#### Scenario: Safe changes applied
- **WHEN** implementation is enabled and gates pass
- **THEN** the agent applies only the scoped changes tied to evidence and runs the required tests, docs checks, examples, or compatibility verification

#### Scenario: Normal optional dependency model preserved
- **WHEN** implementation changes dependency declarations or runtime imports
- **THEN** normal users are not forced to install every vendor SDK as a mandatory dependency

#### Scenario: Credentials are not scraped
- **WHEN** the agent runs locally with user credentials
- **THEN** it relies only on supported vendor SDK authentication mechanisms and does not scrape unsupported credential stores

### Requirement: Recursive self-adaptation
The system SHALL detect when proposed agent-runtime-kit changes affect the SDK
evolution agent's own ability to run future AI stages through agent-runtime-kit.

#### Scenario: Runtime contract impact detected
- **WHEN** an architecture decision proposes changes to `AgentTask`, `AgentResult`, `RuntimeRegistry`, runtime adapter registration, output schemas, event sinks, permission profiles, or unsupported-feature errors
- **THEN** the decision marks the finding as a recursive self-adaptation impact and identifies the agent code, schemas, docs, and tests that must change

#### Scenario: Agent self-adapts safely
- **WHEN** implementation is enabled, reviewer gates pass, and the recursive impact has an obvious compatibility path
- **THEN** the implementation updates the SDK evolution agent's own runtime usage, output schemas, tests, and documentation in the same scoped change

#### Scenario: Recursive migration requires design
- **WHEN** a runtime-layer change could leave the SDK evolution agent unable to execute its own AI stages and the safe migration is not obvious
- **THEN** the agent sets `manual_design_required`, writes the report, and exits without applying partial implementation changes

### Requirement: Independent reviewer
The system SHALL run an independent reviewer stage through agent-runtime-kit
before implementation is considered accepted.

#### Scenario: Reviewer challenges evidence and inference
- **WHEN** the reviewer evaluates the proposed work
- **THEN** it challenges evidence sufficiency, direction-of-travel inference, architecture scope, preservation of vendor-specific behavior, recursive self-adaptation handling, tests, docs, and migration notes

#### Scenario: Reviewer rejection blocks implementation
- **WHEN** the reviewer rejects the evidence, architecture decision, or verification plan
- **THEN** the agent records the rejection in the report and does not apply or finalize implementation changes

### Requirement: Local report and optional draft PR
The system SHALL write a local report for every run and SHALL create draft PRs
only when explicitly configured.

#### Scenario: Report contents
- **WHEN** a run completes
- **THEN** the report includes upstream evidence, API snapshots or diffs, direction-of-travel analysis, architecture decision, recursive self-adaptation impact, implementation summary, test results, uncertainty, reviewer output, and a manual review checklist

#### Scenario: Draft PR creation is explicit
- **WHEN** draft PR creation is not explicitly configured or GitHub authentication is unavailable
- **THEN** the agent does not create or update a PR

#### Scenario: Draft PR is review-gated
- **WHEN** draft PR creation is enabled and authenticated
- **THEN** the PR is opened or updated as a draft, includes the report content, and is not auto-merged

### Requirement: Test and documentation coverage
The system SHALL include tests and documentation for the SDK evolution agent's
core behavior.

#### Scenario: Test coverage exists
- **WHEN** the change is implemented
- **THEN** tests cover version detection, freshness cutoff bypassing, evidence bundling, API diffing, direction-analysis schema, architecture-decision schema, recursive self-adaptation detection, reviewer rejection criteria, local command behavior, and report or PR generation

#### Scenario: Run documentation exists
- **WHEN** the change is implemented
- **THEN** docs explain how to run the local agent with local credentials, how AI calls are routed through agent-runtime-kit, how reports are reviewed, and how optional draft PR creation is configured
