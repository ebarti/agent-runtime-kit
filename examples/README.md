# Runnable examples

Run these commands from the repository root with Python 3.10+ after installing
the package locally (`python -m pip install -e .`), or use `uv run --locked`
before each `python` command. The commands in this table use the core package,
need no credentials, and make no provider calls.

| Command | Expected behavior |
| --- | --- |
| `python -m examples.basic_offline` | Prints `offline example complete`. The fake returns a fixture; it does not read files. |
| `python -m examples.structured_output` | Prints `example: Python` after validating a dataclass result. |
| `python -m examples.events` | Prints `agent.task.started`, `agent.tool.completed`, then `agent.task.completed`. |
| `python -m examples.diagnostics fake --probe-readiness` | Prints available package and `ready-to-attempt`; no task runs. |
| `python -m examples.diagnostics codex` | Reports package availability only. This does not inspect credentials or run a task. |

The following commands use a real vendor runtime. Install its extra and follow
the [provider setup guide](../docs/quickstart.md#choose-a-provider). A provider
call may incur charges and may run tools; the examples request strict,
read-only filesystem access through the adapter. Check the [permission
mapping](../docs/capability-matrix.md) for each provider's exact behavior.

| Command | Expected behavior |
| --- | --- |
| `python -m examples.diagnostics claude --probe-readiness` | Prints package and setup readiness, without running an agent task. |
| `python -m examples.provider_task claude` | Checks setup and runs one Claude task if ready; prints its output on success. |
| `python -m examples.provider_task codex` | Runs one Codex task after the same checks. |
| `python -m examples.provider_task antigravity` | Runs one Antigravity task after the same checks. |
| `python -m examples.run_same_task claude codex` | Runs the same task sequentially through the two named providers. Nothing runs unless you name it. |

Use `--attempt-indeterminate` with the provider task or comparison command to
explicitly attempt a provider whose bounded setup probe could not determine
readiness. A confirmed `not_ready` status still stops. Missing extras, failed
results, and provider exceptions print an error and yield a nonzero exit code.
The commands choose the provider's native model default; set a task model in
your application when you need a fixed model.

The comparison command closes cached runtime resources at the end of the run.
No example stores secrets or starts a paid call during import or `--help`.
See [recipes](../docs/recipes.md) for error handling and input compatibility.
