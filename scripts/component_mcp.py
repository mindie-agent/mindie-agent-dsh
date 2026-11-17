#!/usr/bin/env python3
"""[Role] Expose deterministic MindIE components through DSH's native MCP client.
[Invariants] Discovery needs no task or knowledge state; concurrent `tools/call` requests run on a bounded resident thread pool (`AD-03` fixed: never rejects parallel status/read calls with `-32000`).
[Timeouts & Locks] Per-request cancellation event map (`requestId -> Event`); EOF cancels in-flight calls and drains within 6.0s.
[Side-Effect Policy] Only `tools/call` executes components; protocol stdout writes are serialized under `output_lock`; resident process holds a shared runtime lease.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import threading

import component_tools
import runtime_lease

TIMEOUT_ERROR_CODE = -32001
MAX_CONCURRENT_CALLS = 16


def _timeout_error(identifier, stage: str, error: str = "TimeoutError") -> dict:
    """One honest JSON-RPC timeout reply; the connection stays usable."""
    return {
        "jsonrpc": "2.0",
        "id": identifier,
        "error": {
            "code": TIMEOUT_ERROR_CODE,
            "message": "component call timed out; not completed",
            "data": {"status": "timeout", "stage": stage, "error_type": error, "automatic_retry": False},
        },
    }


def handle(message, *, cancel=None):
    if "id" not in message:
        return None
    identifier, method = message["id"], message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        result = {
            "protocolVersion": params.get("protocolVersion", "2025-11-25"),
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {
                "name": "mindie-agent-dsh-tools",
                "version": json.loads((component_tools.paths.PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))["version"],
            },
        }
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
        except TimeoutError:
            raise
        except Exception as exc:  # noqa: BLE001
            result = component_tools.response(
                {"status": "failed", "error": f"{type(exc).__name__}: {exc}", "automatic_retry": False},
                is_error=True,
            )
    else:
        return {"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": identifier, "result": result}


def serve(reader=None, writer=None, *, on_after_call=None):
    reader, writer = reader or sys.stdin, writer or sys.stdout
    output_lock = threading.Lock()
    state_lock = threading.Lock()
    active_cancels: dict[object, threading.Event] = {}
    futures = []

    def send(payload):
        if payload is not None:
            with output_lock:
                writer.write(json.dumps(payload, ensure_ascii=False) + "\n")
                writer.flush()

    def dispatch_call(request, event):
        req_id = request["id"]
        try:
            response = handle(request, cancel=event)
        except TimeoutError as exc:
            response = _timeout_error(req_id, "execute", type(exc).__name__)
        finally:
            with state_lock:
                active_cancels.pop(req_id, None)
        if on_after_call is not None:
            on_after_call()
        send(response)

    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS, thread_name_prefix="mindie-component-call")
    try:
        for line in reader:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
                continue
            if not isinstance(message, dict):
                send({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request must be an object"}})
                continue
            if message.get("method") == "notifications/cancelled":
                target_id = (message.get("params") or {}).get("requestId")
                with state_lock:
                    target = active_cancels.get(target_id)
                if target is not None:
                    target.set()
                continue
            if message.get("method") != "tools/call" or "id" not in message:
                send(handle(message))
                continue
            req_id = message["id"]
            event = threading.Event()
            with state_lock:
                active_cancels[req_id] = event
            futures[:] = [f for f in futures if not f.done()]
            futures.append(pool.submit(dispatch_call, message, event))
    finally:
        with state_lock:
            events = list(active_cancels.values())
        for ev in events:
            ev.set()
        pool.shutdown(wait=False, cancel_futures=True)
        deadline_threads = [f for f in futures if not f.done()]
        for fut in deadline_threads:
            try:
                fut.result(timeout=6.0)
            except TimeoutError as exc:
                raise TimeoutError("component call did not drain after cancellation") from exc
            except Exception:  # noqa: BLE001
                pass


def serve_selected_serialized(select, *, reader=None, writer=None):
    from remote_dev.mcp.catalog_refresh import CatalogRefresh, selected_rpc

    reader, writer = reader or sys.stdin, writer or sys.stdout
    output_lock = threading.Lock()
    state_lock = threading.Lock()
    active_cancels: dict[object, threading.Event] = {}
    futures = []

    def send(value):
        with output_lock:
            writer.write(json.dumps(value, ensure_ascii=False) + "\n")
            writer.flush()

    def revision():
        argv, env = select()
        return tuple(argv), tuple(sorted((env or {}).items()))

    refresh = CatalogRefresh(revision, send)

    def dispatch(message, cancel, *, is_tool_call=False):
        req_id = message.get("id")
        stage = "select_runtime"
        try:
            argv, env = select()
            stage = "dispatch_helper"
            response = selected_rpc(
                argv, message, env=env, cancel=cancel, timeout=660 if message.get("method") == "tools/call" else 15,
            )
        except TimeoutError as exc:
            response = _timeout_error(req_id, stage, type(exc).__name__)
        except Exception as exc:  # noqa: BLE001
            response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": str(exc)}],
                    "structuredContent": {
                        "error_type": type(exc).__name__,
                        "automatic_retry": False,
                        "stage": stage,
                        "status": "cancelled" if isinstance(exc, InterruptedError) else "helper_failed",
                        "execution": (
                            "outcome_unconfirmed"
                            if stage == "dispatch_helper" and message.get("method") == "tools/call"
                            else "not_started"
                        ),
                    },
                },
            }
        finally:
            if is_tool_call:
                with state_lock:
                    active_cancels.pop(req_id, None)
        refresh.check()
        send(response)

    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_CALLS, thread_name_prefix="mindie-selected-call")
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
                target_id = (message.get("params") or {}).get("requestId")
                with state_lock:
                    target = active_cancels.get(target_id)
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
                req_id = message["id"]
                event = threading.Event()
                with state_lock:
                    active_cancels[req_id] = event
                futures[:] = [f for f in futures if not f.done()]
                futures.append(pool.submit(dispatch, message, event, is_tool_call=True))
            else:
                dispatch(message, None)
    finally:
        refresh.close()
        with state_lock:
            events = list(active_cancels.values())
        for ev in events:
            ev.set()
        pool.shutdown(wait=False, cancel_futures=True)
        for fut in [f for f in futures if not f.done()]:
            try:
                fut.result(timeout=6.0)
            except TimeoutError as exc:
                raise TimeoutError("selected MCP call did not drain after cancellation") from exc
            except Exception:  # noqa: BLE001
                pass
    return 0


def _serve_resident_or_selected() -> int:
    config = component_tools.paths.config_file()
    value = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    configured_py = value.get("python") or sys.executable
    configured_pkg = Path(value.get("package_dir") or component_tools.paths.PACKAGE_DIR)
    if component_tools.paths.same_path(configured_py, sys.executable) and component_tools.paths.same_path(
        configured_pkg, component_tools.paths.PACKAGE_DIR
    ):
        os.environ["MINDIE_DSH_CONFIG"] = str(config)
        serve()
        return 0

    def selection():
        cur_val = json.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
        pkg = Path(cur_val.get("package_dir") or component_tools.paths.PACKAGE_DIR)
        return [
            cur_val.get("python") or sys.executable,
            "-X",
            "utf8",
            str(pkg / "scripts/component_mcp.py"),
            "--once",
        ], {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "MINDIE_DSH_CONFIG": str(config),
        }

    return serve_selected_serialized(selection)


if __name__ == "__main__":
    runtime_lease.hold_runtime_lease()
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
        _serve_resident_or_selected()
