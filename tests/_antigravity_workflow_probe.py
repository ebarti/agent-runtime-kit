"""Run a local Antigravity workflow against a deterministic model stub."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from google.antigravity import Agent, LocalOpenAIAgentConfig

from agent_runtime_kit import AgentTask, PermissionMode, PermissionProfile
from agent_runtime_kit.adapters.antigravity import AntigravityAgentRuntime, _capability_policy


async def _run_case(
    root: Path, name: str, permissions: PermissionProfile, *, allowed: bool
) -> None:
    workspace = root / name
    workspace.mkdir()
    marker = workspace / "created.txt"
    workflow = workspace / "workflow.py"
    workflow.write_text(
        "try:\n"
        f"    result = await call_tool('write_file', path={str(marker)!r}, content='marker')\n"
        "    log('RESULT: ' + str(result))\n"
        "except Exception as exc:\n"
        "    log('ERROR: ' + str(exc))\n",
        encoding="utf-8",
    )

    class Handler(BaseHTTPRequestHandler):
        calls = 0

        def log_message(self, _format: str, *args: Any) -> None:
            pass

        def do_POST(self) -> None:
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            type(self).calls += 1
            if type(self).calls == 1:
                arguments = json.dumps(
                    {
                        "ScriptPath": str(workflow),
                        "Description": "Check workflow permissions",
                        "WaitMsBeforeAsync": 10000,
                        "toolSummary": "Permission check",
                        "toolAction": "Checking permissions",
                    }
                )
                delta = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "run_workflow", "arguments": arguments},
                        }
                    ],
                }
                finish = "tool_calls"
            else:
                delta = {"role": "assistant", "content": "Finished."}
                finish = "stop"
            chunks = [
                {
                    "id": "fake",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fake",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                },
                {
                    "id": "fake",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fake",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                },
            ]
            data = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks)
            data += "data: [DONE]\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(data.encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        runtime = AntigravityAgentRuntime(api_key="unused", data_dir=workspace)
        task = AgentTask(goal="Check permissions", permissions=permissions)
        capabilities, policies = _capability_policy(runtime.kind, task, runtime._load_sdk())
        config = LocalOpenAIAgentConfig(
            model="fake",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            workspaces=[str(workspace)],
            app_data_dir=str(workspace / "app"),
            save_dir=str(workspace / "sessions"),
            capabilities=capabilities,
            policies=policies,
        )
        async with Agent(config) as agent:
            result = await agent.beta.run_workflow(script_path=str(workflow))
        if allowed:
            assert marker.read_text(encoding="utf-8") == "marker", result.output
        else:
            assert not marker.exists(), result.output
            assert "ERROR:" in result.output, result.output
        print(f"{name}: {'allowed' if allowed else 'refused'}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def _main(root: Path) -> None:
    await _run_case(
        root,
        "allowed",
        PermissionProfile(
            mode=PermissionMode.PERMISSIVE, allowed_tools=("run_workflow", "create_file")
        ),
        allowed=True,
    )
    await _run_case(
        root,
        "allowlist",
        PermissionProfile(mode=PermissionMode.PERMISSIVE, allowed_tools=("run_workflow",)),
        allowed=False,
    )
    await _run_case(
        root,
        "denylist",
        PermissionProfile(mode=PermissionMode.PERMISSIVE, disallowed_tools=("create_file",)),
        allowed=False,
    )


if __name__ == "__main__":
    asyncio.run(asyncio.wait_for(_main(Path(sys.argv[1])), timeout=90))
