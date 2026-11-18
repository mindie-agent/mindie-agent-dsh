#!/usr/bin/env python3
"""Deterministic stdlib cold-start seed launcher for `mindie-agent-dsh`.

[Role]
Verifies `assets/mindie-harness-seed.pyz` against `assets/seed-lock.json` and
dispatches to the validated `mindie_harness` runtime or cold-start seed archive.

[Invariants]
- Zero non-stdlib imports; enforces SHA-256 + byte-size verification before executing `.pyz`.
- Uses `CREATE_NO_WINDOW` (`0x08000000`) on Windows; never swallows `TimeoutError`.

[Timeouts & Locks]
- Fast passive hook path returns `{}` without spawning a child or opening SQLite when community sharing is disabled.

[Side-Effect Policy]
- Executes `mindie_harness.cli` or `mindie-harness-seed.pyz` with `-I -X utf8`.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

HOST, CLI_MOD = "dsh", "mindie_harness.cli"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _cfg_home() -> Path:
    if v := os.environ.get("MINDIE_DSH_CONFIG_HOME") or os.environ.get("MINDIE_CONFIG_HOME"):
        return Path(v)
    base = (
        os.environ.get("APPDATA") or (Path.home() / "AppData/Roaming")
        if os.name == "nt"
        else os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    )
    return Path(base) / "mindie-agent" / "dsh"


def _data_home() -> Path:
    if v := os.environ.get("MINDIE_DSH_DATA_HOME") or os.environ.get("MINDIE_DATA_HOME"):
        return Path(v)
    base = (
        os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local")
        if os.name == "nt"
        else os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share")
    )
    return Path(base) / "mindie-agent" / "dsh"


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_bytes().decode("utf-8-sig")) if p.is_file() else None
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None


def _verify_seed(pyz: Path, lock: Path) -> None:
    doc = _read_json(lock)
    if not isinstance(doc, dict) or doc.get("schema") != "mindie-harness-seed/1":
        raise SystemExit("Invalid seed-lock.json")
    h, sz = hashlib.sha256(), 0
    with pyz.open("rb") as fh:
        while blk := fh.read(65536):
            h.update(blk)
            sz += len(blk)
    if sz != doc.get("size_bytes") or h.hexdigest() != doc.get("sha256"):
        raise SystemExit("mindie-harness-seed.pyz SHA-256 mismatch")


def _active_python(cfg_path: Path) -> Path | None:
    cfg = _read_json(cfg_path)
    if isinstance(cfg, dict) and isinstance(cfg.get("python"), str):
        py = Path(cfg["python"])
        if py.is_file():
            return py
    return None


def _canonical_path(value: str) -> str:
    resolved = os.path.realpath(value)
    return os.path.normcase(resolved) if os.name == "nt" else resolved


def _within_roots(candidate: str, roots: list[object]) -> bool:
    if not candidate or not os.path.isabs(candidate):
        return False
    target = _canonical_path(candidate)
    for root in roots:
        if not isinstance(root, str) or not os.path.isabs(root):
            continue
        base = _canonical_path(root)
        try:
            if os.path.commonpath([base, target]) == base:
                return True
        except ValueError:
            continue
    return False


def _stop_event_authorized(cfg_path: Path, raw: bytes) -> bool:
    if not raw or len(raw) > 262144:
        return False
    cfg_dir = _cfg_home()
    cfg = _read_json(cfg_path)
    eng_override = os.environ.get("MINDIE_DSH_ENGINE_CONFIG")
    eng_path = (
        Path(eng_override)
        if eng_override
        else (
            Path(cfg["engine_config"])
            if isinstance(cfg, dict) and isinstance(cfg.get("engine_config"), str) and cfg["engine_config"]
            else cfg_dir / "engine.json"
        )
    )
    eng_doc = _read_json(eng_path)
    comm_path = (
        Path(eng_doc["community_config"])
        if isinstance(eng_doc, dict) and isinstance(eng_doc.get("community_config"), str) and eng_doc["community_config"]
        else cfg_dir / "mindie-community.json"
    )
    comm = _read_json(comm_path)
    if (
        not isinstance(comm, dict)
        or comm.get("enabled") is not True
        or not isinstance(comm.get("project_roots"), list)
        or not comm["project_roots"]
    ):
        return False
    consent_path = (
        Path(comm["consent_config"])
        if isinstance(comm.get("consent_config"), str) and comm["consent_config"]
        else cfg_dir / "mindie-consent.json"
    )
    consent = _read_json(consent_path)
    if not isinstance(consent, dict) or consent.get("choice") != "contribute":
        return False
    try:
        event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(event, dict):
        return False
    workspace = event.get("cwd") or event.get("workspace_root")
    return isinstance(workspace, str) and bool(workspace.strip()) and _within_roots(workspace, comm["project_roots"])


def main() -> int:
    argv = sys.argv[1:]
    cfg_path = Path(os.environ.get("MINDIE_DSH_CONFIG") or (_cfg_home() / "config.json"))
    state_dir = Path(os.environ.get("MINDIE_DSH_STATE_DIR") or (_data_home() / "state"))
    if len(argv) >= 2 and argv[0] == "hook" and argv[1] == "stop":
        raw = sys.stdin.buffer.read(262145)
        if _stop_event_authorized(cfg_path, raw):
            runner = _active_python(cfg_path) or Path(sys.executable)
            capture_script = PLUGIN_ROOT / "scripts" / "capture_stop.py"
            if capture_script.is_file():
                child_env = {
                    **os.environ,
                    "PYTHONUTF8": "1",
                    "PYTHONIOENCODING": "utf-8",
                    "MINDIE_DSH_CONFIG": str(cfg_path),
                    "MINDIE_DSH_STATE_DIR": str(state_dir),
                }
                try:
                    subprocess.run(
                        [str(runner), "-X", "utf8", str(capture_script)],
                        input=raw,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=child_env,
                        timeout=15.0,
                        check=False,
                        creationflags=CREATE_NO_WINDOW,
                    )
                except TimeoutError:
                    raise
                except (OSError, subprocess.SubprocessError):
                    pass
        sys.stdout.buffer.write(b"{}\n")
        sys.stdout.buffer.flush()
        return 0
    py = _active_python(cfg_path)
    if py is not None:
        return subprocess.run(
            [str(py), "-I", "-X", "utf8", "-m", CLI_MOD, "--host", HOST, "--plugin-root", str(PLUGIN_ROOT), "--config", str(cfg_path), *argv],
            creationflags=CREATE_NO_WINDOW,
        ).returncode
    if argv[:2] == ["hook", "session-start"]:
        try:
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            tmp = state_dir / f".pending.tmp.{os.getpid()}"
            tmp.write_text(json.dumps({"schema": "mindie-pending-update/1", "host": HOST, "reason": "session_start", "requested_at": time.time()}) + "\n", encoding="utf-8")
            os.replace(tmp, state_dir / "pending-update-check.json")
        except TimeoutError:
            raise
        except OSError:
            pass
        sys.stdout.buffer.write(b"{}\n")
        sys.stdout.buffer.flush()
        return 0
    pyz, lock = PLUGIN_ROOT / "assets" / "mindie-harness-seed.pyz", PLUGIN_ROOT / "assets" / "seed-lock.json"
    _verify_seed(pyz, lock)
    return subprocess.run(
        [sys.executable, "-I", "-X", "utf8", str(pyz), "--host", HOST, "--plugin-root", str(PLUGIN_ROOT), "--config", str(cfg_path), "--cli-module", CLI_MOD, *argv],
        creationflags=CREATE_NO_WINDOW,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
