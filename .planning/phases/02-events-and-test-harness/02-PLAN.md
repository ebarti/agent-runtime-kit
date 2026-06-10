# Phase 2: Events and Test Harness - Plan

## Plan 02-01: Optional extras skeleton and dependency isolation tests

- Add `claude`, `codex`, `antigravity`, and `all` optional extras.
- Add a test proving core import does not import vendor SDK modules.

## Plan 02-02: Event vocabulary, event sink, and redaction/truncation defaults

- Add normalized event helper functions for task, output, tool, and vendor-turn
  events.
- Add safe best-effort emission.
- Sanitize sensitive and high-volume payload values by default.

## Plan 02-03: Fake SDK harness and adapter contract test utilities

- Add `FakeSDKScenario`, `FakeSDKHarness`, `FakeSDKRuntime`, and
  `RecordingEventSink`.
- Cover success, failure, timeout, structured output, session id, and tool
  event scenarios.
