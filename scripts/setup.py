#!/usr/bin/env python3
"""[Role] One-step runtime provisioner for the DeepSeek Harness adapter.
[Invariants] Prepares an independent pinned runtime and immutable engine paths. One adapter pointer selects all domains after their old owners confirm idle retirement.
[Timeouts & Locks] Every subprocess receives the remaining shared setup budget; timeout cleanup has a separate five-second allowance.
[Side-Effect Policy] Populates data_home()/runtimes, prepares immutable engine JSON, publishes config.json atomically, and stages updater scripts. Saved consent remains unchanged.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402
import setup_process  # noqa: E402

SCHEMA = "mindie-dsh-setup/1"
ADAPTER_SCHEMA = "mindie-dsh-adapter/1"
ENGINE_SCHEMA = "mindie-knowledge-engine/1"
VERSION = "0.1.3"
RUN_DEADLINE: float | None = None
INSTALL_RUNTIME_DIR: Path | None = None
DOMAIN = "vllm-ascend"
FEED = {
    "repository": "mindie-agent/knowledge-vllm-ascend",
    "ref": "main",
    "domain": DOMAIN,
    "interval_seconds": 300,
}
PUBLIC_FEEDS = [
    dict(FEED),
    {
        "repository": "mindie-agent/knowledge-operators",
        "ref": "main",
        "domain": "operators",
        "interval_seconds": 300,
    },
]
COMPONENTS = {
    "mindie-knowledge": "0.13.3",
    "remote-dev": "0.10.6",
    "mindie-diagnostics": "0.5.3",
}


class SetupError(RuntimeError):
    pass


def runtime_dir() -> Path:
    return INSTALL_RUNTIME_DIR or mp.venv_dir()


def runtime_python() -> Path:
    return (runtime_dir() / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            if INSTALL_RUNTIME_DIR is not None else mp.venv_python())


def runtime_identity() -> str:
    """Bind dependency pins and native Python code, including same-version repairs."""
    files = [mp.PACKAGE_DIR / name for name in ('pyproject.toml', 'uv.lock', 'package.json')]
    files.extend(sorted((mp.PACKAGE_DIR / 'scripts').glob('*.py')))
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(mp.PACKAGE_DIR)).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def prepared_engine_path(engine=None) -> Path:
    if INSTALL_RUNTIME_DIR is None:
        return mp.engine_config()
    directory = mp.config_home() / 'engines' / INSTALL_RUNTIME_DIR.parent.name
    if engine is not None:
        directory /= hashlib.sha256(json.dumps(engine, sort_keys=True).encode()).hexdigest()[:20]
    return directory / 'engine.json'


def progress(message: str) -> None:
    print("setup: " + message, file=sys.stderr, flush=True)


def run(
    argv: list[str],
    *,
    timeout: int,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    if RUN_DEADLINE is not None:
        remaining = RUN_DEADLINE - time.monotonic()
        if remaining <= 0:
            raise SetupError("setup exceeded its total time budget")
        timeout = min(timeout, remaining)
    try:
        return setup_process.run(
            argv, cwd=str(cwd) if cwd else None, env=env,
            timeout=timeout,
            input_text=input_text,
        )
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"command exceeded {timeout}s: {' '.join(argv)}") from exc
    except subprocess.SubprocessError as exc:
        raise SetupError(f"command tree cleanup failed: {exc}") from exc
    except OSError as exc:
        raise SetupError(f"could not run {argv[0]}: {exc}") from exc


def tail(text: str, limit: int = 4000) -> str:
    text = text.strip()
    return text if len(text) <= limit else "..." + text[-limit:]


def write_private(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def copy_private(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise SetupError(f"required plugin file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    tmp = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    tmp.write_bytes(source.read_bytes())
    os.replace(tmp, destination)


def adapter_configuration() -> dict[str, Any]:
    """Refresh owned install fields without dropping explicitly registered domains."""
    path = mp.config_file()
    previous = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if not isinstance(previous, dict):
        raise SetupError("existing adapter configuration must be a JSON object")
    config = {**previous,
        "schema": ADAPTER_SCHEMA,
        "version": VERSION,
        "package_dir": str(mp.PACKAGE_DIR),
        "created_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "python": str(runtime_python()),
        "venv_dir": str(runtime_dir()),
        "engine_config": str(prepared_engine_path()),
        "state_dir": str(mp.state_dir()),
        "domain": DOMAIN,
        "components": COMPONENTS,
    }
    if "domain_configs" in previous:
        config["domain_configs"] = previous["domain_configs"]
    return config


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
    venv = runtime_dir()
    mp.ensure_private_dir(mp.data_home())
    env = {**os.environ, "UV_PROJECT_ENVIRONMENT": str(venv)}
    env.setdefault("UV_NO_PROGRESS", "1")
    if "UV_SYSTEM_CERTS" not in env:
        env.setdefault("UV_NATIVE_TLS", "true")
    argv = [
        "uv", "sync", "--frozen", "--quiet", "--no-dev",
        "--project", str(mp.PACKAGE_DIR), "--python", python or "3.11",
    ]
    progress(f"building locked runtime at {venv}")
    completed = run(argv, timeout=timeout, env=env, cwd=mp.PACKAGE_DIR)
    if completed.returncode != 0:
        raise SetupError("uv sync failed. " + tail(completed.stderr or completed.stdout))
    if not runtime_python().is_file():
        raise SetupError(f"uv sync completed without creating {runtime_python()}")


def probe_runtime(*, timeout: int = 120) -> dict[str, Any]:
    completed = run(
        [str(runtime_python()), str(HERE / "runtime_probe.py")],
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise SetupError("runtime import/FTS5 probe failed. " + tail(completed.stderr or completed.stdout))
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise SetupError("runtime probe returned an unexpected result: " + tail(completed.stdout)) from exc
    if not isinstance(payload, dict):
        raise SetupError("runtime probe returned a non-object")
    versions = payload.get("versions") or {}
    if missing := [name for name in COMPONENTS if not versions.get(name)]:
        raise SetupError("missing runtime components: " + ", ".join(missing))
    return payload


def install_redactor(*, timeout: int = 300) -> str:
    progress("installing the deterministic transcript redactor")
    completed = run(
        [str(runtime_python()), "-m", "mindie_knowledge.loop.transcript_redaction"],
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise SetupError("could not install the transcript redactor. " + tail(completed.stderr or completed.stdout))
    path = completed.stdout.strip().splitlines()[-1].strip() if completed.stdout.strip() else ""
    if not path or not Path(path).is_absolute() or not Path(path).is_file():
        raise SetupError("the redactor installer did not return an existing absolute executable: " + tail(completed.stdout))
    return path


def engine_configuration(redactor: str) -> dict[str, Any]:
    adapters = runtime_dir().parent / 'capture' if INSTALL_RUNTIME_DIR is not None else mp.data_home() / 'capture'
    for name in ("dsh_transcript.py", "dsh_organizer.py", "mindie_paths.py"):
        copy_private(mp.PACKAGE_DIR / "scripts" / name, adapters / name)
    value = {
        "root": str(mp.data_home()),
        "domain": DOMAIN,
        "admission_path": str(mp.data_home() / "admission.sqlite3"),
        "capture_mode": "public-transcript",
        "transcript_adapter": str(adapters / "dsh_transcript.py"),
        "redactor_executable": redactor,
        "summary_command": [str(runtime_python()), str(adapters / "dsh_organizer.py")],
        "community_config": str(mp.config_home() / "mindie-community.json"),
        "service_launch_context": {"paths": {}, "environment": ["MINDIE_DIAGNOSTICS_CONFIG"]},
        "feeds": [dict(FEED)],
    }
    previous = json.loads(mp.config_file().read_text()) if mp.config_file().is_file() else {}
    if previous.get('engine_config'):
        original = json.loads(Path(previous['engine_config']).read_text())
        for key in ('root', 'domain', 'admission_path', 'community_config', 'feeds'):
            if key in original:
                value[key] = original[key]
    return value


def prepare_domains(adapter, engine):
    request = dict(adapter=adapter, engine=engine,
                   config_directory=str(Path(adapter['engine_config']).parent / 'domains'),
                   authority_directory=str(mp.config_home() / 'knowledge-domains'),
                   consent_config=str(mp.config_home() / 'mindie-consent.json'),
                   domains=PUBLIC_FEEDS)
    completed = run([str(runtime_python()), '-m', 'mindie_knowledge.loop.domain_catalog'],
                    input_text=json.dumps(request), timeout=90)
    if completed.returncode:
        raise SetupError('domain preparation failed: ' + tail(completed.stderr))
    return json.loads(completed.stdout)['domain_configs']


def install_updater() -> dict[str, Any]:
    directory = mp.data_home() / "update"
    for name in ("updater.py", "component_update.py", "native_update.py", "mindie_paths.py", "setup_process.py", "dsh_diagnostics.py"):
        source = mp.PACKAGE_DIR / "scripts" / name
        destination = directory / name
        try:
            if not (destination.is_file() and destination.read_bytes() == source.read_bytes()):
                copy_private(source, destination)
        except PermissionError:
            if not destination.is_file():
                raise
    config = mp.config_home() / "update.json"
    if not config.is_file():
        write_private(config, {"enabled": True, "interval_minutes": 5})
    else:
        previous = json.loads(config.read_text(encoding='utf-8'))
        if previous.get('interval_hours') == 6 and 'interval_minutes' not in previous:
            previous.pop('interval_hours')
            write_private(config, dict(previous, interval_minutes=5))
    return {"script": str(directory / "updater.py"), "enabled": True, "interval_minutes": 5}


def publish_binding(previous, adapter, engine, *, activate=True) -> dict:
    path = Path(adapter['engine_config'])
    if path.exists():
        if json.loads(path.read_text()) != engine:
            raise SetupError('prepared engine path contains different immutable config')
    else:
        write_private(path, engine)
    if not activate:
        return dict(status='prepared')
    outer = min(35, RUN_DEADLINE - time.monotonic()) if RUN_DEADLINE is not None else 35
    if outer <= 5:
        raise SetupError('setup has no remaining idle handoff and cleanup budget')
    completed = run([str(runtime_python()), str(HERE / 'install_binding.py')],
                    input_text=json.dumps(dict(path=str(mp.config_file()), previous=previous,
                                               candidate=adapter, timeout=outer - 5)), timeout=outer)
    if completed.returncode:
        raise SetupError('runtime activation failed: ' + tail(completed.stderr))
    result = json.loads(completed.stdout)
    if result['status'] != 'active':
        raise SetupError('runtime activation did not complete: ' + json.dumps(result, sort_keys=True))
    return result


def report_setup_failure(error: Exception) -> None:
    if not runtime_python().is_file():
        progress("diagnostic storage unavailable: runtime installation did not complete")
        return
    try:
        result = run([str(runtime_python()), str(mp.PACKAGE_DIR / "scripts" / "dsh_diagnostics.py"),
                      "record", "--operation", "setup", "--category", type(error).__name__], timeout=5)
        progress("diagnostic: " + result.stdout.strip())
    except TimeoutError:
        raise
    except (OSError, SetupError) as exc:
        progress("diagnostic storage unavailable: " + type(exc).__name__)


def main(argv: list[str] | None = None) -> int:
    global RUN_DEADLINE, INSTALL_RUNTIME_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=None, help="interpreter to pass to uv sync (default 3.11)")
    parser.add_argument("--dry-run", action="store_true", help="validate prerequisites and print the plan only")
    parser.add_argument("--install-timeout", type=int, default=900, help="seconds allowed for uv sync")
    parser.add_argument("--total-timeout", type=float, default=None,
                        help="shared budget for all setup subprocesses")
    parser.add_argument("--component-manifest", type=Path,
                        help="verified independent component generation prepared by the updater")
    parser.add_argument("--prepare-only", action="store_true",
                        help="validate and prepare without publishing the native binding")
    args = parser.parse_args(argv)
    RUN_DEADLINE = (time.monotonic() + args.total_timeout
                    if args.total_timeout is not None else None)

    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(mp.diagnostics_config())
    started = time.time()
    try:
        check_host()
        INSTALL_RUNTIME_DIR = mp.data_home() / 'runtimes' / runtime_identity() / 'venv'
        if args.component_manifest:
            from mindie_knowledge.component_runtime import source_baseline
            from mindie_knowledge.component_releases import runtime_identity as effective_identity
            from mindie_knowledge.runtime_inventory import verify, verify_lock
            manifest = json.loads(args.component_manifest.read_text(encoding="utf-8"))
            effective_identity(source_baseline(mp.PACKAGE_DIR), manifest)
            INSTALL_RUNTIME_DIR = args.component_manifest.parent / 'runtime/.venv'
            if Path(sys.prefix).resolve() != INSTALL_RUNTIME_DIR.resolve():
                raise SetupError('component setup must run in its prepared interpreter')
            verify_lock(manifest)
            verify(manifest['runtime'])
        paths = {
            "package_dir": str(mp.PACKAGE_DIR),
            "data_home": str(mp.data_home()),
            "config_home": str(mp.config_home()),
            "config_file": str(mp.config_file()),
            "engine_config": str(prepared_engine_path()),
            "state_dir": str(mp.state_dir()),
            "venv_dir": str(runtime_dir()),
            "venv_python": str(runtime_python()),
        }
        if args.dry_run:
            print(
                json.dumps(
                    {
                        "schema": SCHEMA, "status": "dry-run", "version": VERSION,
                        "python": sys.version.split()[0], "uv": shutil.which("uv"),
                        "components": COMPONENTS, "paths": paths,
                        "knowledge": {"status": "inactive"},
                    },
                    ensure_ascii=False, indent=2, sort_keys=True,
                )
            )
            return 0

        for d in (mp.data_home(), mp.config_home(), mp.state_dir()):
            mp.ensure_private_dir(d)
        previous = json.loads(mp.config_file().read_text()) if mp.config_file().is_file() else {}
        probe = None
        if args.component_manifest:
            probe = probe_runtime()
        if not args.component_manifest and previous.get('venv_dir') == str(runtime_dir()):
            try:
                probe = probe_runtime()
            except SetupError:
                # A repair builds separately so existing processes keep their
                # original imports until the complete replacement is ready.
                INSTALL_RUNTIME_DIR = INSTALL_RUNTIME_DIR.parent.with_name(
                    INSTALL_RUNTIME_DIR.parent.name + '-repair-' + uuid.uuid4().hex[:8]) / 'venv'
        if probe is None:
            sync_runtime(args.python, timeout=args.install_timeout)
            probe = probe_runtime()
        adapter = adapter_configuration()
        adapter["components"] = probe["versions"]
        redactor = install_redactor()
        engine = engine_configuration(redactor)
        adapter['engine_config'] = str(prepared_engine_path(engine))
        adapter['domain'] = engine['domain']
        adapter['domain_configs'] = prepare_domains(adapter, engine)
        publication = publish_binding(previous, adapter, engine, activate=not args.prepare_only)
        if args.prepare_only:
            print(json.dumps(dict(status='prepared', components=probe['versions'], paths=paths)))
            return 0
        paths.update(engine_config=adapter['engine_config'], venv_dir=adapter['venv_dir'], venv_python=adapter['python'])
        knowledge = {"status": "inactive"}
        update = install_updater()
        reporting_process = run(
            [str(runtime_python()), str(mp.PACKAGE_DIR / "scripts" / "dsh_diagnostics.py"), "activate"],
            timeout=90,
        )
        if reporting_process.returncode:
            raise SetupError("reporter activation failed: " + tail(reporting_process.stderr))
        try:
            reporting = json.loads(reporting_process.stdout)
        except ValueError as exc:
            raise SetupError("reporter activation did not return JSON") from exc
        status = "ready"
        if reporting.get("status") not in {"running", "disabled", "needs_choice"}:
            status = "partial"
        summary = {
            "schema": SCHEMA, "status": status, "version": VERSION,
            "package_dir": str(mp.PACKAGE_DIR), "python": probe.get("python"),
            "sqlite": probe.get("sqlite"), "executable": probe.get("executable"),
            "components": probe.get("versions"), "paths": paths,
            "knowledge": knowledge, "update": update, "reporting": reporting, "activation": publication,
            "contribution_notice": "One contribution choice controls public knowledge sharing and sanitized fault reports. Existing choices are preserved; legacy choices require explicit confirmation before reporting.",
            "elapsed_seconds": round(time.time() - started, 2),
            "next": (
                "Enable the mindie-agent-dsh bundle in DSH (Plugins page or plugin_manager), "
                "open a new session, and type /mindie-agent <task>."
            ),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except SetupError as exc:
        progress("failed: " + str(exc))
        report_setup_failure(exc)
        return 1
    except TimeoutError:
        raise
    except OSError as exc:
        progress("failed: " + f"{type(exc).__name__}: {exc}")
        report_setup_failure(exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
