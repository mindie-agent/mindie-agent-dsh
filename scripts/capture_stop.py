#!/usr/bin/env python3
"""[Role] Detached DSH Stop-hook capture handoff helper.
[Invariants] Reads at most 256 KiB from stdin; never emits to stdout; never blocks on knowledge service startup.
[Timeouts & Locks] Bounded by capture_hook local SQLite WAL handoff; rotates capture.log at 1 MiB.
[Side-Effect Policy] Appends one capture row via mindie_knowledge.loop.cli.capture_hook and logs receipt to capture.log.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402
import dsh_diagnostics as diagnostics  # noqa: E402

MAX_INPUT = 256 * 1024
LOG_LIMIT = 1024 * 1024


def log_path() -> Path:
    return mp.state_dir() / "capture.log"


def append_log(value: dict) -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            os.replace(path, path.with_suffix(".1"))
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        pass


def diagnose(stage: str, error: Exception) -> dict:
    return diagnostics.failure("capture", stage, error)


def main() -> int:
    diagnostics.bind_environment()
    started = time.time()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("capture envelope exceeds limit")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("capture envelope is not an object")
        config = mp.engine_config()
        if not config.is_file():
            raise FileNotFoundError("runtime engine configuration is missing")
        from mindie_knowledge.loop.cli import capture_hook

        receipt = capture_hook(str(config), event)
        if isinstance(receipt, dict):
            receipt = {k: receipt.get(k) for k in ("stage", "reason", "cause", "capture_id", "duplicate")}
        entry = {"time": time.time(), "elapsed": round(time.time() - started, 3), "receipt": receipt}
        if isinstance(receipt, dict) and (
            receipt.get("stage") in {"unavailable", "failed", "error"}
            or receipt.get("stage") == "rejected" and receipt.get("reason") != "no-capturable-material"
            or receipt.get("cause") == "wake-failed"
        ):
            if projected := diagnose(
                str(receipt.get("stage") or "capture"),
                RuntimeError(str(receipt.get("reason") or "capture not accepted")),
            ):
                entry["diagnostic"] = projected
        append_log(entry)
        return 0
    except Exception as exc:  # noqa: BLE001
        entry = {
            "time": time.time(),
            "elapsed": round(time.time() - started, 3),
            "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
        }
        if projected := diagnose("hook", exc):
            entry["diagnostic"] = projected
        append_log(entry)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
