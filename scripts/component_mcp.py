#!/usr/bin/env python3
"""[Role] Expose deterministic MindIE components through DSH's native MCP client.
[Invariants] Discovery needs no runtime state; knowledge tools never enter this catalog.
[Timeouts & Locks] One in-flight call; cancellation/EOF stop its child within the five-second cleanup budget.
[Side-Effect Policy] Only tools/call executes components; protocol writes are serialized.
"""

from __future__ import annotations

import json
import sys
import threading

import component_tools


def handle(message, *, cancel=None):
    if "id" not in message:
        return None
    identifier, method = message["id"], message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", "2025-11-25"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "mindie-agent-dsh-tools", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": component_tools.catalog()}
    elif method == "tools/call":
        try:
            arguments = params.get("arguments", {})
            if not isinstance(arguments, dict):
                raise ValueError("tool arguments must be an object")
            result = component_tools.execute(params.get("name"), arguments, cancel=cancel)
        except Exception as exc:
            result = component_tools.response(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}",
                 "automatic_retry": False}, is_error=True)
    else:
        return {"jsonrpc": "2.0", "id": identifier,
                "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def serve(reader=None, writer=None):
    reader, writer = reader or sys.stdin, writer or sys.stdout
    output_lock = threading.Lock()
    worker, cancellation, request_id = None, None, None

    def send(payload):
        if payload is not None:
            with output_lock:
                writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
                writer.flush()

    try:
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32700, "message": str(exc)}})
                continue
            if not isinstance(message, dict):
                send({"jsonrpc": "2.0", "id": None,
                      "error": {"code": -32600, "message": "request must be an object"}})
                continue
            if message.get("method") == "notifications/cancelled":
                if cancellation is not None and (message.get("params") or {}).get("requestId") == request_id:
                    cancellation.set()
                continue
            if message.get("method") != "tools/call" or "id" not in message:
                send(handle(message))
                continue
            if worker is not None and worker.is_alive():
                send({"jsonrpc": "2.0", "id": message["id"],
                      "error": {"code": -32000, "message": "component call already running; not executed"}})
                continue
            cancellation, request_id = threading.Event(), message["id"]
            worker = threading.Thread(
                target=lambda request=message, event=cancellation: send(handle(request, cancel=event)),
                name="mindie-component-call", daemon=True,
            )
            worker.start()
    finally:
        if worker is not None:
            cancellation.set()
            worker.join(timeout=6.0)
            if worker.is_alive():
                raise TimeoutError("component call did not drain after cancellation")


if __name__ == "__main__":
    serve()
