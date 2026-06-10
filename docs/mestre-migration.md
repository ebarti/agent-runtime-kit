# Mestre Migration Notes

Mestre can adopt `agent-runtime-kit` at the vendor-lane boundary rather than at
the routing boundary.

The public `AgentTask` covers the fields Mestre currently carries into its
vendor runtimes:

- task id and goal
- event sink
- system prompt
- working directory
- MCP stdio server config
- permission profile
- SDK execution count and budget
- session id and resume state
- output schema
- metadata for model ids, provider ids, reasoning effort, prompt receipts, and
  vendor-specific diagnostics

The package deliberately does not absorb Mestre's routing, fallback,
benchmarking, self-improvement, credential store, model registry, or
observability backend. Mestre should keep those layers and translate its
internal task object into `AgentTask` immediately before dispatching to a
runtime.
