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
    except TimeoutError:
        raise
    except OSError as exc:
        print(
            f"MindIE capture receipt write failed: {type(exc).__name__}: {exc}"[:500],
            file=sys.stderr,
        )


def diagnose(stage: str, error: Exception) -> dict:
    return diagnostics.failure("capture", stage, error)


def failure_entry(started: float, error: Exception) -> dict:
    entry = {
        "time": time.time(),
        "elapsed": round(time.time() - started, 3),
        "error": {"type": type(error).__name__, "message": str(error)[:500]},
    }
    if projected := diagnose("hook", error):
        entry["diagnostic"] = projected
    return entry


def _inert_receipt(reason: str) -> dict:
    return {
        "stage": "inert",
        "reason": reason,
        "cause": None,
        "capture_id": None,
        "duplicate": False,
    }


def main() -> int:
    diagnostics.bind_environment()
    started = time.time()
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("capture envelope exceeds limit")
        event = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(event, dict):
            raise ValueError("capture envelope is not an object")
        config = mp.engine_config()
        if not config.is_file():
            raise FileNotFoundError("runtime engine configuration is missing")
        engine_doc = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(engine_doc, dict):
            raise ValueError("runtime engine configuration is not an object")
        from mindie_knowledge.loop import settings as settings_mod

        comm_cfg = engine_doc.get("community_config") or str(mp.config_home() / "mindie-community.json")
        community = settings_mod.load(str(comm_cfg))
        if community.error is None and community.schema_ok:
            if not community.allows_capture():
                append_log({"time": time.time(), "elapsed": round(time.time() - started, 3), "receipt": _inert_receipt("capture-disabled")})
                return 0
            cwd = event.get("cwd")
            if isinstance(cwd, str) and cwd:
                if not os.path.isabs(cwd) or not community.in_scope(cwd):
                    append_log({"time": time.time(), "elapsed": round(time.time() - started, 3), "receipt": _inert_receipt("outside-project-roots")})
                    return 0
                session_id = event.get("session_id")
                if isinstance(session_id, str) and session_id.strip():
                    from mindie_knowledge.loop.activation import Admission

                    adm_path = engine_doc.get("admission_path") or str(Path(mp.data_home()) / "admission.sqlite3")
                    adm_file = Path(adm_path)
                    adm = Admission(str(adm_file))
                    if adm_file.is_file() and adm.inspect(session_id).get("status") == "inactive":
                        append_log({"time": time.time(), "elapsed": round(time.time() - started, 3), "receipt": _inert_receipt("session-deactivated")})
                        return 0
                    adm_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    aligned_cwd = str(Path(cwd).resolve())
                    lease = adm.activate(
                        session_id,
                        project_root=aligned_cwd,
                        root_session=session_id,
                        not_before=0.0,
                    )
                    event = {**event, "cwd": aligned_cwd, "mindie_activation": lease["token"]}
                root_dir, domain = engine_doc.get("root"), engine_doc.get("domain")
                if isinstance(root_dir, str) and root_dir and isinstance(domain, str) and domain:
                    from mindie_knowledge import state_layout

                    if not (state_layout.state_root(root_dir, domain) / state_layout.DATABASE).is_file():
                        from mindie_knowledge.loop.handoff import prepare_schema

                        prepare_schema(str(config))
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
    except TimeoutError as exc:
        append_log(failure_entry(started, exc))
        return 1
    except Exception as exc:  # noqa: BLE001
        append_log(failure_entry(started, exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
