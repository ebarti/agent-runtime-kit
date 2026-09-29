# Deadlines and cancellation

`AgentTask.deadline` is an absolute, timezone-aware `datetime`. It applies to
the whole provider operation, including SDK startup and waiting for a reused
provider process. `AgentKit.run(timeout=...)` is a convenience that converts a
finite, non-negative number of seconds (or `timedelta`) into one absolute
deadline at call time.

```python
from agent_runtime_kit import AgentKit, AgentTaskTimeoutError

try:
    result = await kit.run(
        "codex", goal="Inspect the parser", timeout=30,
        permissions="strict", filesystem="read-only",
    )
except AgentTaskTimeoutError as exc:
    print(exc.task_id, exc.deadline)
```

An expired deadline never starts the vendor SDK (and therefore emits no started
event). A deadline expiry emits an `agent.task.failed` event with
`finish_reason="timed_out"`, cancels the in-flight provider coroutine, gives its
cleanup a bounded five-second grace period, and raises `AgentTaskTimeoutError`
(which is both an `AgentRuntimeError` and `TimeoutError`). Direct adapter calls
honor `AgentTask(deadline=...)` too. If provider teardown remains wedged after
that grace, the runtime instance is quarantined and rejects new runs rather than
overlapping them; it becomes reusable when the detached cleanup finally settles.

To cancel a task started through `AgentKit`, keep its task id and use the same
runtime instance or cached kind. Wait for its started event (or for an early
completion) before calling `cancel()`; scheduling `kit.run()` with
`asyncio.create_task()` does not guarantee that it has registered the run yet.
This example makes a real provider call and may incur charges:

```python
import asyncio

from agent_runtime_kit import AgentKit


async def main() -> None:
    task_id = "index-repository"
    started = asyncio.Event()
    async with AgentKit() as kit:

        @kit.on("agent.task.started")
        def on_started(event):
            if event["attributes"]["task_id"] == task_id:
                started.set()

        running = asyncio.create_task(
            kit.run(
                "claude", goal="Index the repository", task_id=task_id,
                permissions="strict", filesystem="read-only",
            )
        )
        start_wait = asyncio.create_task(started.wait())
        try:
            done, _ = await asyncio.wait(
                {running, start_wait}, return_when=asyncio.FIRST_COMPLETED
            )
            if running in done:
                result = await running  # completed or failed before cancellation
                print(result.finish_reason)
                return
            receipt = await kit.cancel("claude", task_id)
            print(receipt.disposition)
            try:
                result = await running
                print(result.finish_reason)  # it may finish before cancellation takes effect
            except asyncio.CancelledError:
                print("cancelled")
        finally:
            start_wait.cancel()
            await asyncio.gather(start_wait, return_exceptions=True)


asyncio.run(main())
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
