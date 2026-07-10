# Deadlines and cancellation

`AgentTask.deadline` is an absolute, timezone-aware `datetime`. It applies to
the whole provider operation, including SDK startup and waiting for a reused
provider process. `AgentKit.run(timeout=...)` is a convenience that converts a
finite, non-negative number of seconds (or `timedelta`) into one absolute
deadline at call time.

```python
from agent_runtime_kit import AgentKit, AgentTaskTimeoutError

try:
    result = await kit.run("codex", goal="Refactor the parser", timeout=30)
except AgentTaskTimeoutError as exc:
    print(exc.task_id, exc.deadline)
```

An expired deadline never starts the vendor SDK (and therefore emits no started
event). A deadline expiry emits an `agent.task.failed` event with
`finish_reason="timed_out"`, cancels the in-flight provider coroutine, gives its
cleanup a bounded five-second grace period, and raises `AgentTaskTimeoutError`
(which is both an `AgentRuntimeError` and `TimeoutError`). Direct adapter calls
honor `AgentTask(deadline=...)` too.

To cancel a task started through `AgentKit`, keep its task id and use the same
runtime instance or cached kind:

```python
import asyncio

task_id = "index-repository"
running = asyncio.create_task(
    kit.run("claude", goal="Index the repository", task_id=task_id)
)
receipt = await kit.cancel("claude", task_id)

try:
    await running
except asyncio.CancelledError:
    pass

print(receipt.disposition)
```

`cancel()` does not construct a runtime that has not already been cached, and
it does not wait for the cancelled run to settle. Built-in adapters also expose
the same method directly. `CancellationReceipt.disposition` distinguishes a
new request, a repeated request, an inactive id, an unsupported hook, a failed
hook, and a legacy runtime that returned no receipt.

An active `(runtime, task_id)` identifies one run generation. If a legacy
task-id-only cancellation hook is still settling, `AgentKit` keeps that id
reserved rather than allowing the delayed hook to target a replacement run.

A `REQUESTED` receipt confirms only that cancellation was requested at the
runtime coroutine boundary. It does not promise rollback: commands, network
requests, or other tool side effects that completed before cancellation may be
permanent. Await the original run task to observe completion of provider
cleanup before reusing related resources.
