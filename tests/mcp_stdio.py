from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class McpProcess:
    proc: subprocess.Popen
    out_q: "queue.Queue[dict]"

    def send(self, msg: dict) -> None:
        if self.proc.stdin is None:
            raise RuntimeError("McpProcess: stdin is not available")
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def recv(self, *, timeout_s: float = 10.0) -> dict:
        try:
            return self.out_q.get(timeout=timeout_s)
        except queue.Empty as exc:
            raise TimeoutError("Timed out waiting for MCP response") from exc

    def request(self, method: str, params: dict | None = None, *, timeout_s: float = 10.0, req_id: int = 1) -> dict:
        self.send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}})
        while True:
            msg = self.recv(timeout_s=timeout_s)
            if msg.get("id") == req_id:
                return msg

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def start_mcp_server(env: dict[str, str] | None = None) -> McpProcess:
    """
    Start `python -m src.server` (stdio transport) and return a helper.
    Assumes one JSON-RPC message per line on stdout.
    """
    proc = subprocess.Popen(
        ["python", "-m", "src.server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )

    out_q: "queue.Queue[dict]" = queue.Queue()

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                # Non-JSON log line; ignore.
                continue
            if isinstance(obj, dict):
                out_q.put(obj)

    t = threading.Thread(target=_reader, daemon=True)
    t.start()

    return McpProcess(proc=proc, out_q=out_q)


def mcp_initialize(m: McpProcess, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """
    Perform a minimal MCP initialize handshake.
    """
    init = m.request(
        "initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "mtg-tests", "version": "0.0"},
        },
        timeout_s=timeout_s,
        req_id=1,
    )
    # Some servers require an 'initialized' notification.
    m.send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
    return init


def wait_for_tools_list(m: McpProcess, *, timeout_s: float = 10.0) -> list[dict[str, Any]]:
    resp = m.request("tools/list", params={}, timeout_s=timeout_s, req_id=2)
    if "result" not in resp or not isinstance(resp["result"], dict):
        raise TypeError(f"tools/list: expected result object, got: {resp}")
    tools = resp["result"].get("tools")
    if not isinstance(tools, list):
        raise TypeError(f"tools/list: expected tools list, got: {resp}")
    return tools

