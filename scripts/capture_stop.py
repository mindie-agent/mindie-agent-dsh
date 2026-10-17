#!/usr/bin/env python3
"""Bounded DSH Stop handoff.

The Host plugin spawns this helper detached at a turn boundary. It reads one
Stop envelope from stdin, commits one knowledge capture row, and exits. It
never waits for the knowledge service, never emits to stdout, and records the
receipt in a bounded local log for diagnosis.
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

MAX_INPUT = 256 * 1024
LOG_LIMIT = 1024 * 1024


def log_path() -> Path:
    return mp.state_dir() / "capture.log"


def append_log(value: dict) -> None:
    path = log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LOG_LIMIT:
            rotated = path.with_suffix(".1")
            os.replace(path, rotated)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError:
        # A diagnostic log failure must not turn into a second capture or a
        # user-visible turn failure; the capture receipt itself is the authority.
        pass


def main() -> int:
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
            receipt = {"stage": receipt.get("stage"), "reason": receipt.get("reason"),
                       "capture_id": receipt.get("capture_id"), "duplicate": receipt.get("duplicate")}
        append_log({"time": time.time(), "elapsed": round(time.time() - started, 3), "receipt": receipt})
        return 0
    except Exception as exc:  # noqa: BLE001
        append_log(
            {
                "time": time.time(),
                "elapsed": round(time.time() - started, 3),
                "error": {"type": type(exc).__name__, "message": str(exc)[:500]},
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
