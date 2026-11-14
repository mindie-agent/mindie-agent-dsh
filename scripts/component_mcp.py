#!/usr/bin/env python3
"""[Role] Expose deterministic MindIE components through DSH's native MCP client.
[Invariants] Discovery needs no task or knowledge state; the selected profile owns the runtime.
[Timeouts & Locks] One in-flight call; cancellation/EOF stop its child within the five-second cleanup budget.
[Side-Effect Policy] Only tools/call executes components; protocol writes are serialized.
"""

from __future__ import annotations

import json
from pathlib import Path
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
                  "serverInfo": {
                      "name": "mindie-agent-dsh-tools",
                      "version": json.loads((component_tools.paths.PACKAGE_DIR / "package.json")
                                            .read_text(encoding="utf-8"))["version"],
                  }}
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
    state_lock = threading.Lock()
    worker, cancellation, request_id = None, None, None
    in_flight = False

    def send(payload):
        if payload is not None:
            with output_lock:
                writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
                writer.flush()

    def dispatch_call(request, event):
        nonlocal in_flight
        response = handle(request, cancel=event)
        with output_lock:
            with state_lock:
                if request_id == request["id"]:
                    in_flight = False
            if response is not None:
                writer.write(json.dumps(response, ensure_ascii=False) + "\n")
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
                with state_lock:
                    target = (
                        cancellation
                        if in_flight and (message.get("params") or {}).get("requestId") == request_id
                        else None
                    )
                if target is not None:
                    target.set()
                continue
            if message.get("method") != "tools/call" or "id" not in message:
                send(handle(message))
                continue
            with state_lock:
                busy = in_flight
            if busy:
                send({"jsonrpc": "2.0", "id": message["id"],
                      "error": {"code": -32000, "message": "component call already running; not executed"}})
                continue
            if worker is not None:
                worker.join(timeout=1.0)
                if worker.is_alive():
                    send({"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": -32000, "message": "component call already running; not executed"}})
                    continue
            with state_lock:
                cancellation = threading.Event()
                request_id = message["id"]
                in_flight = True
            worker = threading.Thread(
                target=dispatch_call, args=(message, cancellation),
                name="mindie-component-call", daemon=True,
            )
            worker.start()
    finally:
        if worker is not None:
            if cancellation is not None:
                cancellation.set()
            worker.join(timeout=6.0)
            if worker.is_alive():
                raise TimeoutError("component call did not drain after cancellation")


def serve_selected_serialized(select, *, reader=None, writer=None):
    from remote_dev.mcp.catalog_refresh import CatalogRefresh, selected_rpc

    reader, writer = reader or sys.stdin, writer or sys.stdout
    output_lock = threading.Lock()
    state_lock = threading.Lock()
    cancellation = threading.Event()
    worker = None
    request_id = None
    in_flight = False

    def send(value):
        with output_lock:
            writer.write(json.dumps(value, ensure_ascii=False) + "\n")
            writer.flush()

    def revision():
        argv, env = select()
        return tuple(argv), tuple(sorted((env or {}).items()))

    refresh = CatalogRefresh(revision, send)

    def dispatch(message, cancel, *, is_tool_call=False):
        nonlocal in_flight
        stage = "select_runtime"
        try:
            argv, env = select()
            stage = "dispatch_helper"
            response = selected_rpc(
                argv, message, env=env, cancel=cancel,
                timeout=660 if message.get("method") == "tools/call" else 15,
            )
        except Exception as exc:
            response = {"jsonrpc": "2.0", "id": message["id"], "result": {
                "isError": True, "content": [{"type": "text", "text": str(exc)}],
                "structuredContent": {
                    "error_type": type(exc).__name__, "automatic_retry": False,
                    "stage": stage,
                    "status": "cancelled" if isinstance(exc, InterruptedError) else "helper_failed",
                    "execution": ("outcome_unconfirmed" if stage == "dispatch_helper"
                                  and message.get("method") == "tools/call" else "not_started"),
                },
            }}
        refresh.check()
        with output_lock:
            if is_tool_call:
                with state_lock:
                    if request_id == message["id"]:
                        in_flight = False
            writer.write(json.dumps(response, ensure_ascii=False) + "\n")
            writer.flush()

    try:
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("MCP request must be an object")
            except ValueError as exc:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
                continue
            method = message.get("method")
            if method == "notifications/cancelled":
                with state_lock:
                    target = (
                        cancellation
                        if in_flight and (message.get("params") or {}).get("requestId") == request_id
                        else None
                    )
                if target is not None:
                    target.set()
                continue
            if "id" not in message:
                continue
            if method == "initialize":
                argv, env = select()
                response = selected_rpc(argv, message, env=env, timeout=15)
                if isinstance(response.get("result"), dict):
                    response["result"].setdefault("capabilities", {})["tools"] = {"listChanged": True}
                send(response)
                refresh.start()
            elif method == "ping":
                send({"jsonrpc": "2.0", "id": message["id"], "result": {}})
            elif method == "tools/call":
                with state_lock:
                    busy = in_flight
                if busy:
                    send({"jsonrpc": "2.0", "id": message["id"],
                          "error": {"code": -32000, "message": "component call already running; not executed"}})
                    continue
                if worker is not None:
                    worker.join(timeout=1.0)
                    if worker.is_alive():
                        send({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32000, "message": "component call already running; not executed"}})
                        continue
                with state_lock:
                    cancellation = threading.Event()
                    request_id = message["id"]
                    in_flight = True
                worker = threading.Thread(
                    target=dispatch, args=(message, cancellation),
                    kwargs={"is_tool_call": True}, daemon=True,
                )
                worker.start()
            else:
                dispatch(message, None)
    finally:
        refresh.close()
        with state_lock:
            active_cancel = cancellation
        active_cancel.set()
        if worker is not None:
            worker.join(timeout=6)
            if worker.is_alive():
                raise TimeoutError("selected MCP call did not drain after cancellation")
    return 0


if __name__ == "__main__":
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    if "--once" in sys.argv:
        message = json.loads(sys.stdin.readline())
        result = handle(message)
        if result is not None:
            print(json.dumps(result, ensure_ascii=False), flush=True)
    else:
        def selection():
            config = component_tools.paths.config_file()
            value = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
            package = Path(value.get("package_dir") or component_tools.paths.PACKAGE_DIR)
            return [value.get("python") or sys.executable,
                    "-X", "utf8",
                    str(package / "scripts/component_mcp.py"), "--once"], {
                        "PYTHONUTF8": "1",
                        "PYTHONIOENCODING": "utf-8",
                        "MINDIE_DSH_CONFIG": str(config),
                    }

        serve_selected_serialized(selection)
