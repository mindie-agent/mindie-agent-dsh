#!/usr/bin/env python3
"""[Role] Thin DSH setup CLI: `init` writes `dsh.json`/`dsh.engine.json`, handles pre-0.1.26 `updater.py` (`--python`/`--prepare-only`) handoffs, and delegates `sharing`, `doctor`, `update`, and `release-metadata` to `mindie_harness`.
[Invariants] The engine carries an absolute transcript adapter, redactor, and loopback summary command; `--prepare-only` validates/stages without mutating canonical `dsh.json`; every compatibility code path and test id must resolve before emitting `mindie-plugin-release/1`.
[Timeouts & Locks] Init performs atomic local writes and harness-owned GC only; seed bootstrap and doctor/update inherit `mindie_harness` deadlines and locks.
[Side-Effect Policy] Writes only MindIE-owned profile documents and prints one JSON object per command.
"""
from __future__ import annotations

import argparse
import ast
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSION = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
DOMAIN, HARNESS, REPOSITORY = "vllm-ascend", "dsh", "mindie-agent/mindie-agent-dsh"
COMMANDS = frozenset({"init", "install", "sharing", "doctor", "update", "release-metadata"})


def _has_modern_harness() -> bool:
    try:
        return importlib.util.find_spec("mindie_harness.mcp.gateway") is not None
    except (ImportError, ValueError):
        return False


def _profile(config: str | None = None):
    from mindie_harness.paths import ProfileRef

    return ProfileRef.resolve(
        HARNESS,
        plugin_root=PLUGIN_ROOT,
        config_path=config or os.environ.get("MINDIE_AGENT_CONFIG") or os.environ.get("MINDIE_DSH_CONFIG"),
    )


def _emit(value: dict) -> int:
    print(json.dumps(value, sort_keys=True))
    return 0


def _read(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _same_interp(left: str | Path, right: str | Path) -> bool:
    a, b = Path(left), Path(right)
    return os.path.normcase(str(a.parent.resolve() / a.name)) == os.path.normcase(str(b.parent.resolve() / b.name))


def _legacy_config_path(profile) -> Path:
    candidates = []
    if dsh_home := os.environ.get("MINDIE_DSH_CONFIG_HOME"):
        candidates.append(Path(dsh_home).expanduser().resolve() / "config.json")
    candidates.append(profile.config_path.parent / "dsh" / "config.json")
    return next((cand for cand in candidates if cand.is_file()), candidates[0])


def _runtime_python(profile, cfg: dict, *, explicit_python: str | None = None, has_manifest: bool = False) -> str:
    if explicit_python and has_manifest and Path(explicit_python).is_file():
        return str(Path(explicit_python))
    if _has_modern_harness():
        return sys.executable
    configured = cfg.get("python")
    if isinstance(configured, str) and Path(configured).is_file():
        return configured
    try:
        from mindie_harness.api import prepare_runtime

        return str(prepare_runtime(profile).python_path)
    except TimeoutError:
        raise
    except (ImportError, AttributeError, RuntimeError, OSError):
        return sys.executable


def _redactor(profile) -> str:
    explicit = os.environ.get("MINDIE_REDACTOR_EXECUTABLE")
    if explicit and Path(explicit).is_absolute():
        return explicit
    try:
        from mindie_knowledge.loop.transcript_redaction import install_scanner

        return str(install_scanner())
    except TimeoutError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError):
        return shutil.which("gitleaks") or str(profile.data_dir / "bin" / ("gitleaks.exe" if os.name == "nt" else "gitleaks"))


def _init(profile, argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="setup.py init")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--domain", default=None)
    parser.add_argument("--python", default=None)
    parser.add_argument("--total-timeout", type=float, default=None)
    parser.add_argument("--install-timeout", type=float, default=None)
    parser.add_argument("--component-manifest", type=Path, default=None)
    parser.add_argument("--prepare-only", action="store_true")
    opts = parser.parse_args(argv)
    legacy_cfg_path = _legacy_config_path(profile)
    prev_cfg = _read(profile.config_path) or _read(legacy_cfg_path)
    python = _runtime_python(profile, prev_cfg, explicit_python=opts.python, has_manifest=opts.component_manifest is not None)
    commands = prev_cfg.get("component_commands")
    rebound = None
    if isinstance(commands, dict) and commands:
        from mindie_knowledge.component_bindings import managed_commands, rebind_commands, validate_command_floor

        versions = {name: {"version": importlib.metadata.version(name)} for name, _py, _mod in managed_commands(prev_cfg).values()}
        validate_command_floor(prev_cfg, versions)
        rebound = rebind_commands(prev_cfg, python, versions)
    if opts.prepare_only:
        return _emit({
            "status": "prepared",
            "version": VERSION,
            "config_path": str(profile.config_path),
            "python": python,
        })
    engine_path = profile.config_path.with_name("dsh.engine.json")
    prev_ep = prev_cfg.get("engine_config")
    prev_engine = _read(engine_path) or (_read(Path(prev_ep)) if isinstance(prev_ep, str) and os.path.isabs(prev_ep) else {})
    domain = opts.domain or prev_cfg.get("domain") or prev_engine.get("domain") or DOMAIN
    root = opts.root.expanduser().resolve() if opts.root else Path(prev_engine.get("root") or (profile.data_dir / "knowledge"))
    community_path = profile.config_path.parent / "mindie-community.json"
    if legacy_cfg_path.parent != profile.config_path.parent:
        legacy_consent = legacy_cfg_path.parent / "mindie-consent.json"
        if not profile.consent_path.is_file() and legacy_consent.is_file():
            if doc := _read(legacy_consent):
                _write(profile.consent_path, doc)
        legacy_comm = legacy_cfg_path.parent / "mindie-community.json"
        if not community_path.is_file() and legacy_comm.is_file():
            if doc := _read(legacy_comm):
                if "consent_config" in doc:
                    doc["consent_config"] = str(profile.consent_path)
                _write(community_path, doc)
    engine = dict(prev_engine, **{
        "root": str(root), "domain": domain,
        "admission_path": str(prev_engine.get("admission_path") if not opts.root and prev_engine.get("admission_path") else profile.admission_db),
        "capture_mode": "public-transcript", "transcript_adapter": str(HERE / "dsh_transcript.py"),
        "redactor_executable": _redactor(profile),
        "summary_command": [python, "-I", "-X", "utf8", str(HERE / "mindie_launch.py"), "--config", str(profile.config_path), "summary"],
        "community_config": str(community_path),
        "feeds": prev_engine.get("feeds") if isinstance(prev_engine.get("feeds"), list) and prev_engine["feeds"] else [{"repository": "mindie-agent/knowledge-vllm-ascend", "ref": "main", "domain": domain, "interval_seconds": 300}],
    })
    adapter = dict(prev_cfg, **{
        "schema": "mindie-dsh-adapter/1", "host": HARNESS, "version": VERSION, "python": python,
        "venv_dir": str(Path(python).parent.parent), "engine_config": str(engine_path), "state_dir": str(profile.state_dir),
        "config_path": str(profile.config_path), "consent_config": str(profile.consent_path),
        "community_config": engine["community_config"], "package_dir": str(PLUGIN_ROOT),
        "source_root": str(PLUGIN_ROOT), "runtime_scripts": str(HERE), "domain": domain,
    })
    if rebound is not None:
        adapter["component_commands"] = rebound
    _write(engine_path, engine)
    _write(profile.config_path, adapter)
    if legacy_cfg_path != profile.config_path and legacy_cfg_path.is_file():
        _write(legacy_cfg_path, adapter)
    from mindie_harness.api import maintain_runtime

    return _emit({
        "status": "ready", "version": VERSION, "config_path": str(profile.config_path),
        "engine_config": str(engine_path), "python": python, "domain": domain,
        "transcript_adapter": engine["transcript_adapter"],
        "summary_command": engine["summary_command"], "gc": maintain_runtime(profile),
    })


def _sharing(profile, argv: list[str]) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status

    parser = argparse.ArgumentParser(prog="setup.py sharing")
    for flag in ("--status", "--enable", "--local-only", "--disable"):
        parser.add_argument(flag, action="store_true")
    parser.add_argument("--project-root", action="append", default=[])
    parser.add_argument("--repository")
    parser.add_argument("--fork")
    parser.add_argument("--account")
    opts = parser.parse_args(argv)
    if opts.status or not (opts.enable or opts.local_only or opts.disable):
        return _emit(sharing_status(profile, cwd=Path.cwd()))
    choice = "contribute" if opts.enable else "local-only" if opts.local_only else "disabled"
    return _emit(configure_sharing(profile, choice=choice, project_roots=opts.project_root, repository=opts.repository, fork=opts.fork, account=opts.account))


def _doctor(profile, argv: list[str]) -> int:
    from mindie_harness.api import run_host

    parser = argparse.ArgumentParser(prog="setup.py doctor")
    parser.add_argument("--probe-remote", action="store_true")
    parser.add_argument("--cwd")
    parser.add_argument("--timeout", type=float)
    opts = parser.parse_args(argv)
    command = ["--host", HARNESS, "--plugin-root", str(PLUGIN_ROOT), "--config", str(profile.config_path), "doctor"]
    command += ["--probe-remote"] if opts.probe_remote else []
    command += ["--cwd", opts.cwd] if opts.cwd else []
    command += ["--timeout", str(opts.timeout)] if opts.timeout is not None else []
    return run_host(command)


def _update(profile, argv: list[str]) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime

    parser = argparse.ArgumentParser(prog="setup.py update")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--channel-base-url")
    parser.add_argument("--background", action="store_true")
    opts = parser.parse_args(argv)
    return _emit(update_runtime(profile, force=opts.force, offline=opts.offline, channel_base_url=opts.channel_base_url, background=opts.background))


def _declares(path: Path, parts: list[str]) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for part in parts:
        node = next((item for item in body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part), None)
        if node is None:
            return False
        body = node.body
    return True


def _release(argv: list[str]) -> int:
    from mindie_knowledge.releases import validate_compatibility, validate_release_metadata

    parser = argparse.ArgumentParser(prog="setup.py release-metadata")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--tests", type=Path, default=None)
    opts = parser.parse_args(argv)
    source = opts.source.expanduser().resolve()
    declaration = validate_compatibility(json.loads((source / "harness-compatibility.json").read_text(encoding="utf-8-sig")), harness_name=HARNESS)
    problems = []
    for entry in declaration["interface_registry"].values():
        for raw in [*entry["code_paths"], *entry["test_ids"]]:
            relative, _, symbol = raw.partition("::") if "::" in raw else raw.partition(":")
            path = (source / relative).resolve()
            if not path.is_file() or (symbol and path.suffix == ".py" and not _declares(path, symbol.split("."))):
                problems.append(raw)
    if problems:
        raise SystemExit("unresolved release metadata targets: " + "; ".join(sorted(set(problems))))
    return _emit(validate_release_metadata(
        {"schema": "mindie-plugin-release/1", "version": VERSION, "source": {"repository": REPOSITORY, "sha": opts.revision}, "compatibility": declaration},
        repository=REPOSITORY, harness_name=HARNESS,
    ))


def _bootstrap_legacy_seed(raw_args: list[str], config: str | None) -> int:
    import mindie_launch

    cfg_path = mindie_launch._config_path(config)
    py = mindie_launch._active_python(cfg_path)
    if py is None or _same_interp(py, sys.executable):
        seed = PLUGIN_ROOT / "assets" / "mindie-harness-seed.pyz"
        mindie_launch._verify_seed(seed, PLUGIN_ROOT / "assets" / "seed-lock.json")
        stage_cfg = mindie_launch._state_dir() / "legacy-seed-prepare.json"
        stage_cfg.unlink(missing_ok=True)
        try:
            boot = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", str(seed), "--host", HARNESS, "--plugin-root", str(PLUGIN_ROOT), "--config", str(stage_cfg)],
                stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW,
            )
            if boot.returncode != 0:
                return boot.returncode
            staged_py = _read(stage_cfg).get("python")
            py = Path(staged_py) if isinstance(staged_py, str) and Path(staged_py).is_file() else None
        finally:
            stage_cfg.unlink(missing_ok=True)
    if py is None or _same_interp(py, sys.executable):
        raise RuntimeError("MindIE seed bootstrap did not yield a modern runtime interpreter")
    return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=CREATE_NO_WINDOW).returncode


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args, config = list(raw_args), None
    while "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args):
            raise SystemExit("--config requires a path")
        config, args = args[index + 1], args[:index] + args[index + 2:]
    command = args[0] if args and args[0] in COMMANDS else "init"
    rest = args[1:] if args and args[0] in COMMANDS else args
    if command == "release-metadata":
        return _release(rest)
    if not _has_modern_harness():
        return _bootstrap_legacy_seed(raw_args, config)
    profile = _profile(config)
    if command in {"init", "install"}:
        return _init(profile, rest)
    return {"sharing": _sharing, "doctor": _doctor, "update": _update}[command](profile, rest)


if __name__ == "__main__":
    raise SystemExit(main())
