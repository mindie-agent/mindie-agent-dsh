#!/usr/bin/env python3
"""Provision the pinned MindIE runtime for the DeepSeek Harness adapter.

The install is deliberately one explicit step: build the locked Python runtime,
generate the knowledge-engine configuration, install the deterministic
transcript redactor, and synchronize the configured public knowledge feed. The
DSH bundle itself remains a thin composition; this script owns no plugin
lifecycle.

Usage:
    uv run --quiet --no-project --python 3.11 scripts/setup.py
    uv run --quiet --no-project --python 3.11 scripts/setup.py --no-sync
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402

SCHEMA = "mindie-dsh-setup/1"
ADAPTER_SCHEMA = "mindie-dsh-adapter/1"
ENGINE_SCHEMA = "mindie-knowledge-engine/1"
VERSION = "0.0.1"
DOMAIN = "vllm-ascend"
FEED = {
    "repository": "mindie-agent/knowledge-vllm-ascend",
    "ref": "main",
    "domain": DOMAIN,
    "interval_seconds": 300,
}
COMPONENTS = {
    "mindie-knowledge": "0.11.0",
    "remote-dev": "0.9.5",
    "mindie-diagnostics": "0.4.1",
}


class SetupError(RuntimeError):
    pass


def progress(message: str) -> None:
    print("setup: " + message, file=sys.stderr, flush=True)


def run(
    argv: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(
            f"command exceeded {timeout}s: {' '.join(argv)}"
        ) from exc
    except OSError as exc:
        raise SetupError(f"could not run {argv[0]}: {exc}") from exc


def tail(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        descriptor = -1
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass


def copy_private(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SetupError(f"required plugin file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix="." + destination.name + ".", dir=str(destination.parent)
    )
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as out, source.open("rb") as stream:
            shutil.copyfileobj(stream, out)
            out.flush()
            os.fsync(out.fileno())
        descriptor = -1
        os.replace(temporary, destination)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass


def check_host() -> None:
    if sys.version_info < (3, 11):
        raise SetupError(
            f"Python 3.11+ is required for setup, found {sys.version.split()[0]}. "
            "Run: uv run --quiet --no-project --python 3.11 scripts/setup.py"
        )
    if shutil.which("uv") is None:
        raise SetupError(
            "uv is required. Install it first: https://docs.astral.sh/uv/getting-started/installation/"
        )


def sync_runtime(python: str | None, *, timeout: int) -> None:
    venv = mp.venv_dir()
    mp.ensure_private_dir(mp.data_home())
    env = dict(os.environ)
    env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    env.setdefault("UV_NO_PROGRESS", "1")
    argv = [
        "uv",
        "sync",
        "--frozen",
        "--quiet",
        "--no-dev",
        "--project",
        str(mp.PACKAGE_DIR),
    ]
    argv.extend(["--python", python or "3.11"])
    progress(f"building locked runtime at {venv}")
    completed = run(argv, timeout=timeout, env=env, cwd=mp.PACKAGE_DIR)
    if completed.returncode != 0:
        raise SetupError(
            "uv sync failed. " + tail(completed.stderr or completed.stdout)
        )
    if not mp.venv_python().is_file():
        raise SetupError(f"uv sync completed without creating {mp.venv_python()}")


def probe_runtime(*, timeout: int = 120) -> dict[str, Any]:
    probe = r"""
import importlib.metadata as md
import json
import sqlite3
import sys

import mindie_knowledge
import remote_dev

db = sqlite3.connect(":memory:")
try:
    db.execute("CREATE VIRTUAL TABLE probe USING fts5(body, content='', contentless_delete=1)")
    db.execute("INSERT INTO probe(rowid, body) VALUES (1, 'alpha')")
    if db.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall() != [(1,)]:
        raise RuntimeError("FTS5 insert/MATCH probe failed")
    db.execute("UPDATE probe SET body='beta' WHERE rowid=1")
    if db.execute("SELECT rowid FROM probe WHERE probe MATCH 'alpha'").fetchall():
        raise RuntimeError("FTS5 contentless_delete probe failed")
finally:
    db.close()

def version(name):
    try:
        return md.version(name)
    except Exception:
        return None

print(json.dumps({
    "python": sys.version.split()[0],
    "sqlite": sqlite3.sqlite_version,
    "executable": sys.executable,
    "versions": {
        "mindie-knowledge": version("mindie-knowledge"),
        "remote-dev": version("remote-dev"),
        "mindie-diagnostics": version("mindie-diagnostics"),
    },
}))
"""
    completed = run([str(mp.venv_python()), "-c", probe], timeout=timeout)
    if completed.returncode != 0:
        raise SetupError(
            "runtime import/FTS5 probe failed. "
            + tail(completed.stderr or completed.stdout)
        )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise SetupError("runtime probe returned an unexpected result: " + tail(completed.stdout)) from exc
    if not isinstance(payload, dict):
        raise SetupError("runtime probe returned a non-object")
    versions = payload.get("versions") or {}
    mismatched = [
        f"{name}: expected {expected}, found {versions.get(name)}"
        for name, expected in COMPONENTS.items()
        if versions.get(name) != expected
    ]
    if mismatched:
        raise SetupError("runtime component versions do not match the release pins: " + "; ".join(mismatched))
    return payload


def install_redactor(*, timeout: int = 300) -> str:
    progress("installing the deterministic transcript redactor")
    completed = run(
        [str(mp.venv_python()), "-m", "mindie_knowledge.loop.transcript_redaction"],
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise SetupError(
            "could not install the transcript redactor. "
            + tail(completed.stderr or completed.stdout)
        )
    path = completed.stdout.strip().splitlines()[-1].strip() if completed.stdout.strip() else ""
    if not path or not Path(path).is_absolute() or not Path(path).is_file():
        raise SetupError(
            "the redactor installer did not return an existing absolute executable: "
            + tail(completed.stdout)
        )
    return path


def engine_configuration(redactor: str) -> dict[str, Any]:
    capture_dir = mp.data_home() / "capture"
    adapter = capture_dir / "dsh_transcript.py"
    copy_private(mp.PACKAGE_DIR / "scripts" / "dsh_transcript.py", adapter)
    return {
        "root": str(mp.data_home()),
        "domain": DOMAIN,
        "admission_path": str(mp.data_home() / "admission.sqlite3"),
        "capture_mode": "public-transcript",
        "transcript_adapter": str(adapter),
        "redactor_executable": redactor,
        "summary_command": [str(mp.venv_python()), str(mp.PACKAGE_DIR / "scripts" / "dsh_organizer.py")],
        "community_config": str(mp.config_home() / "mindie-community.json"),
        "feeds": [dict(FEED)],
    }


def install_updater() -> dict[str, Any]:
    """Install the stable updater and default to background checks."""
    directory = mp.data_home() / "update"
    for name in ("updater.py", "mindie_paths.py"):
        source = mp.PACKAGE_DIR / "scripts" / name
        destination = directory / name
        try:
            if destination.is_file() and destination.read_bytes() == source.read_bytes():
                continue
            copy_private(source, destination)
        except PermissionError:
            # Windows may still have the running updater script open. The
            # existing stable copy remains valid; the next setup can refresh it.
            if not destination.is_file():
                raise
    config = mp.config_home() / "update.json"
    if not config.is_file():
        write_private(config, {"enabled": True, "interval_hours": 6})
    return {"script": str(directory / "updater.py"), "enabled": True, "interval_hours": 6}


def stop_existing_service() -> None:
    """Best-effort retirement before a configuration rewrite.

    A running service whose configuration fingerprint differs from the new
    engine config would otherwise refuse the next start. The outcome is
    reported, never hidden; setup continues because the runtime itself is
    still usable.
    """

    engine = mp.engine_config()
    if not engine.is_file() or not mp.venv_python().is_file():
        return
    completed = run(
        [
            str(mp.venv_python()),
            "-m",
            "mindie_knowledge.loop.cli",
            "stop",
            "--config",
            str(engine),
        ],
        timeout=60,
    )
    if completed.returncode != 0:
        progress(
            "warning: an existing knowledge service could not be stopped: "
            + tail(completed.stderr or completed.stdout, 500)
        )


def sync_feed(*, timeout: int) -> dict[str, Any]:
    progress("synchronizing the public knowledge feed")
    completed = run(
        [
            str(mp.venv_python()),
            "-m",
            "mindie_knowledge.loop.cli",
            "sync",
            "--config",
            str(mp.engine_config()),
        ],
        timeout=timeout,
    )
    if completed.returncode != 0:
        return {
            "status": "unavailable",
            "error": tail(completed.stderr or completed.stdout),
        }
    raw = completed.stdout.strip()
    try:
        rows = json.loads(raw)
    except ValueError:
        return {
            "status": "unavailable",
            "error": "sync returned non-JSON output: " + tail(raw or completed.stderr),
        }
    if not isinstance(rows, list):
        return {"status": "unavailable", "error": "sync returned a non-list result"}
    feeds: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        feeds.append(
            {
                key: row.get(key)
                for key in ("repository", "status", "commit", "revision", "checked")
                if key in row
            }
        )
    ok = bool(feeds) and all(
        item.get("status") in {"synced", "unchanged"} for item in feeds
    )
    result: dict[str, Any] = {"status": "ready" if ok else "unavailable", "feeds": feeds}
    if not ok:
        result["error"] = "one or more feeds did not report synced/unchanged"
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=None, help="interpreter to pass to uv sync (default 3.11)")
    parser.add_argument("--no-sync", action="store_true", help="do not synchronize the knowledge feed")
    parser.add_argument("--require-sync", action="store_true", help="exit nonzero when feed sync is unavailable")
    parser.add_argument("--dry-run", action="store_true", help="validate prerequisites and print the plan only")
    parser.add_argument("--sync-timeout", type=int, default=900, help="seconds allowed for one feed sync")
    parser.add_argument("--install-timeout", type=int, default=900, help="seconds allowed for uv sync")
    args = parser.parse_args(argv)

    started = time.time()
    try:
        check_host()
        paths = {
            "package_dir": str(mp.PACKAGE_DIR),
            "data_home": str(mp.data_home()),
            "config_home": str(mp.config_home()),
            "config_file": str(mp.config_file()),
            "engine_config": str(mp.engine_config()),
            "state_dir": str(mp.state_dir()),
            "venv_dir": str(mp.venv_dir()),
            "venv_python": str(mp.venv_python()),
        }
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "status": "dry-run",
                        "version": VERSION,
                        "python": sys.version.split()[0],
                        "uv": shutil.which("uv"),
                        "components": COMPONENTS,
                        "paths": paths,
                        "sync": "skipped" if args.no_sync else "planned",
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0

        mp.ensure_private_dir(mp.data_home())
        mp.ensure_private_dir(mp.config_home())
        mp.ensure_private_dir(mp.state_dir())
        sync_runtime(args.python, timeout=args.install_timeout)
        probe = probe_runtime()
        redactor = install_redactor()
        engine = engine_configuration(redactor)
        stop_existing_service()
        write_private(mp.engine_config(), engine)
        adapter = {
            "schema": ADAPTER_SCHEMA,
            "version": VERSION,
            "created_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
            "python": str(mp.venv_python()),
            "venv_dir": str(mp.venv_dir()),
            "engine_config": str(mp.engine_config()),
            "state_dir": str(mp.state_dir()),
            "domain": DOMAIN,
            "components": COMPONENTS,
            "capture": {"enabled": False, "reason": "DSH capture is not implemented in 0.0.1"},
        }
        write_private(mp.config_file(), adapter)

        if args.no_sync:
            knowledge: dict[str, Any] = {"status": "skipped"}
        else:
            knowledge = sync_feed(timeout=args.sync_timeout)

        update = install_updater()
        status = "ready" if knowledge.get("status") in {"ready", "skipped"} else "partial"
        summary = {
            "schema": SCHEMA,
            "status": status,
            "version": VERSION,
            "package_dir": str(mp.PACKAGE_DIR),
            "python": probe.get("python"),
            "sqlite": probe.get("sqlite"),
            "executable": probe.get("executable"),
            "components": probe.get("versions"),
            "paths": paths,
            "knowledge": knowledge,
            "update": update,
            "elapsed_seconds": round(time.time() - started, 2),
            "next": (
                "Enable the mindie-agent-dsh bundle in DSH (Plugins page or plugin_manager), "
                "open a new session, and type /mindie-agent <task>."
            ),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        if args.require_sync and knowledge.get("status") not in {"ready", "skipped"}:
            progress("knowledge feed sync is unavailable (--require-sync)")
            return 1
        return 0
    except SetupError as exc:
        progress("failed: " + str(exc))
        return 1
    except OSError as exc:
        progress("failed: " + f"{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
