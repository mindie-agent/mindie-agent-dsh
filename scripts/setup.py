#!/usr/bin/env python3
"""[Role] Thin DSH setup CLI: `init` preserves revoked sessions across `admission_path` migration, delegates 9-domain binding under `.dsh.json.update.lock`, and delegates `sharing`, `doctor`, `update`, and `release-metadata`.
[Invariants] Offline `init` never invokes network downloads or deletes prior `domain_configs` before `retire_changed`; `--prepare-only` never mutates `dsh.json`; contradictory `sharing` flags fail (`exit 2`).
[Timeouts & Locks] Serializes `init` under `.dsh.json.update.lock` (`<= 5.0s`); SQLite revocation reads use `busy_timeout=5000`; seed/doctor/update inherit `mindie_harness` deadlines.
[Side-Effect Policy] Writes MindIE-owned profile/domain documents atomically and prints one JSON object per command.
"""
from __future__ import annotations
import argparse, ast, hashlib, importlib.metadata, importlib.util, json, os, secrets, shutil, sqlite3, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))
PLUGIN_ROOT = HERE.parent
CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VERSION = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
DOMAIN, HARNESS, REPOSITORY = "vllm-ascend", "dsh", "mindie-agent/mindie-agent-dsh"
COMMANDS = frozenset({"init", "install", "sharing", "doctor", "update", "release-metadata"})

def _has_modern_harness() -> bool:
    spec = importlib.util.find_spec("mindie_harness")
    locs = list(spec.submodule_search_locations or ()) if spec is not None else []
    return any((Path(loc) / "mcp" / "gateway.py").is_file() for loc in locs)

def _is_win_store_stub(cand: Path) -> bool:
    if os.name != "nt": return False
    if "\\microsoft\\windowsapps\\" in str(cand).replace("/", "\\").lower(): return True
    try: return cand.stat().st_size == 0
    except TimeoutError: raise
    except OSError: return True

def _profile(config: str | None = None):
    from mindie_harness.paths import ProfileRef
    return ProfileRef.resolve(HARNESS, plugin_root=PLUGIN_ROOT, config_path=config or os.environ.get("MINDIE_AGENT_CONFIG") or os.environ.get("MINDIE_DSH_CONFIG"))

def _emit(value: dict) -> int:
    print(json.dumps(value, sort_keys=True)); return 0

def _read(path: Path) -> dict:
    try: value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except TimeoutError: raise
    except (OSError, ValueError): return {}
    return value if isinstance(value, dict) else {}

def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)

def _same_interp(left: str | Path, right: str | Path) -> bool:
    a, b = Path(left), Path(right)
    return os.path.normcase(str(a.parent.resolve() / a.name)) == os.path.normcase(str(b.parent.resolve() / b.name))

def _legacy_config_path(profile) -> Path:
    candidates = [Path(dsh_home).expanduser().resolve() / "config.json"] if (dsh_home := os.environ.get("MINDIE_DSH_CONFIG_HOME")) else []
    candidates.append(profile.config_path.parent / "dsh" / "config.json")
    return next((cand for cand in candidates if cand.is_file()), candidates[0])

def _runtime_python(cfg: dict, *, explicit_python: str | None = None, has_manifest: bool = False) -> str:
    if explicit_python and has_manifest and Path(explicit_python).is_file() and not _is_win_store_stub(Path(explicit_python)):
        return str(Path(explicit_python))
    if _has_modern_harness(): return sys.executable
    configured = cfg.get("python")
    return configured if isinstance(configured, str) and Path(configured).is_file() and not _is_win_store_stub(Path(configured)) else sys.executable

def _redactor(profile) -> str:
    explicit = os.environ.get("MINDIE_REDACTOR_EXECUTABLE")
    if explicit and Path(explicit).is_absolute(): return explicit
    found = shutil.which("gitleaks")
    if found and Path(found).is_absolute(): return found
    return str(profile.data_dir / "cache" / "gitleaks" / "8.30.1" / ("gitleaks.exe" if os.name == "nt" else "gitleaks"))

def _migrate_revoked_sessions(profile, *cfg_docs: dict) -> None:
    from mindie_knowledge.loop.activation import Admission
    target, raw_paths = profile.admission_db.resolve(), []
    for doc in cfg_docs:
        if not isinstance(doc, dict): continue
        if isinstance(ap := doc.get("admission_path"), str) and ap: raw_paths.append(ap)
        if isinstance(ep := doc.get("engine_config"), str) and os.path.isabs(ep) and isinstance(eap := _read(Path(ep)).get("admission_path"), str) and eap:
            raw_paths.append(eap)
        if isinstance(routes := doc.get("domain_configs"), dict):
            raw_paths.extend(rap for rp in routes.values() if isinstance(rp, str) and os.path.isabs(rp) and isinstance(rap := _read(Path(rp)).get("admission_path"), str) and rap)
    revoked, attempts, seen = {}, [], set()
    for raw in raw_paths:
        cand = Path(raw).expanduser()
        if cand.suffix.lower() in {".json", ".toml", ".yaml", ".yml"} or not cand.is_file(): continue
        resolved = cand.resolve()
        if resolved == target or resolved in seen: continue
        seen.add(resolved); db = None
        try:
            db = sqlite3.connect(resolved, timeout=5.0); db.row_factory = sqlite3.Row; db.execute("PRAGMA busy_timeout=5000")
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "leases" in tables:
                for row in db.execute("SELECT * FROM leases WHERE enabled=0").fetchall():
                    d = dict(row)
                    if isinstance(sid := d.get("session"), str) and sid.strip():
                        revoked[sid] = (sid, str(d.get("token") or secrets.token_hex(32)), int(d.get("failures") or 0), d.get("project_root"), d.get("root_session") or sid, d.get("activated_at"), int(d.get("entry_enabled") or 0))
            if "sessions" in tables:
                for row in db.execute("SELECT * FROM sessions").fetchall():
                    d = dict(row); sid = d.get("session") or d.get("session_id")
                    if isinstance(sid, str) and sid.strip() and (d.get("status") == "inactive" or d.get("enabled") == 0):
                        revoked.setdefault(sid, (sid, str(d.get("token") or secrets.token_hex(32)), int(d.get("failures") or 0), d.get("project_root"), d.get("root_session") or sid, d.get("activated_at"), 0))
            if "attempts" in tables and revoked:
                attempts.extend((r[0], r[1], r[2], r[3]) for r in db.execute("SELECT session, kind, identity, created FROM attempts").fetchall() if r[0] in revoked)
        except TimeoutError: raise
        except (OSError, sqlite3.Error): continue
        finally:
            if db is not None: db.close()
    if not revoked: return
    def _copy(dst_db):
        for sid, tok, fail, proot, rsid, act, ent in revoked.values():
            dst_db.execute("INSERT INTO leases(session, token, enabled, failures, project_root, root_session, activated_at, entry_enabled) VALUES(?,?,0,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET enabled=0", (sid, tok, fail, proot, rsid, act, ent))
        for item in attempts: dst_db.execute("INSERT OR IGNORE INTO attempts VALUES(?,?,?,?)", item)
    Admission(target)._write(_copy, initialize_missing=True)

def _with_summary_command(profile, res: dict, summary_cmd: list[str]) -> tuple[str, dict[str, str]]:
    from mindie_knowledge.loop import domain_catalog
    from mindie_knowledge.loop.handoff import prepare_schema
    default_ep = Path(res["engine_config"])
    default_doc = _read(default_ep)
    if default_doc.get("summary_command") == summary_cmd:
        return str(default_ep), dict(res["domain_configs"])
    updated = dict(default_doc, summary_command=summary_cmd)
    text = json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    engines_root = (profile.config_path.parent / "engines" / profile.host).resolve()
    new_ep = engines_root / hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] / f"{DOMAIN}.engine.json"
    if not new_ep.is_file() or new_ep.read_text(encoding="utf-8") != text:
        new_ep.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = new_ep.with_name(f".{new_ep.name}.{os.getpid()}.tmp")
        try: tmp.write_text(text, encoding="utf-8"); os.replace(tmp, new_ep)
        finally: tmp.unlink(missing_ok=True)
    prepare_schema(new_ep)
    sanitized = {k: v for k, v in (res.get("domain_configs") or {}).items() if k != DOMAIN and isinstance(v, str) and Path(v).is_file()}
    prepared = domain_catalog.prepare({"engine_config": str(new_ep), "domain_configs": sanitized}, updated, config_directory=str(engines_root), authority_directory=str((profile.config_path.parent / "authorities" / profile.host).resolve()), consent_config=str(profile.consent_path.resolve()), public_feeds=True)
    return str(new_ep), dict(prepared["domain_configs"], **{DOMAIN: str(new_ep)})

def _init(profile, argv: list[str]) -> int:
    from mindie_harness.api import maintain_runtime
    from mindie_harness.locks import FileLock
    from mindie_harness.runtime.knowledge_binding import ensure_knowledge_binding
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
    prev_probe = _read(profile.config_path) or _read(legacy_cfg_path)
    python = _runtime_python(prev_probe, explicit_python=opts.python, has_manifest=opts.component_manifest is not None)
    rebound = None
    if isinstance(commands := prev_probe.get("component_commands"), dict) and commands:
        from mindie_knowledge.component_bindings import managed_commands, rebind_commands, validate_command_floor
        versions = {name: {"version": importlib.metadata.version(name)} for name, _py, _mod in managed_commands(prev_probe).values()}
        validate_command_floor(prev_probe, versions)
        rebound = rebind_commands(prev_probe, python, versions)
    if opts.prepare_only:
        return _emit({"status": "prepared", "version": VERSION, "config_path": str(profile.config_path), "python": python})
    lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
    lock.acquire()
    prev_env_root = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
    try:
        old_canonical = _read(profile.config_path)
        prev_cfg = old_canonical or _read(legacy_cfg_path)
        engine_path = profile.config_path.with_name("dsh.engine.json")
        prev_ep = prev_cfg.get("engine_config")
        prev_engine = _read(Path(prev_ep)) if isinstance(prev_ep, str) and os.path.isabs(prev_ep) and Path(prev_ep).is_file() else _read(engine_path)
        _migrate_revoked_sessions(profile, old_canonical, prev_cfg, {"admission_path": prev_engine.get("admission_path")})
        domain = opts.domain or prev_cfg.get("domain") or prev_engine.get("domain") or DOMAIN
        root = opts.root.expanduser().resolve() if opts.root else Path(prev_engine.get("root") or prev_cfg.get("knowledge_root") or (profile.data_dir / "knowledge")).resolve()
        community_path = profile.config_path.parent / "mindie-community.json"
        if legacy_cfg_path.parent != profile.config_path.parent:
            legacy_consent, legacy_comm = legacy_cfg_path.parent / "mindie-consent.json", legacy_cfg_path.parent / "mindie-community.json"
            if not profile.consent_path.is_file() and legacy_consent.is_file() and (doc := _read(legacy_consent)): _write(profile.consent_path, doc)
            if not community_path.is_file() and legacy_comm.is_file() and (doc := _read(legacy_comm)):
                if "consent_config" in doc: doc["consent_config"] = str(profile.consent_path)
                _write(community_path, doc)
        summary_cmd = [python, "-I", "-X", "utf8", str(HERE / "mindie_launch.py"), "--config", str(profile.config_path), "summary"]
        engine_doc = dict(prev_engine, **{
            "root": str(root), "domain": domain, "admission_path": str(profile.admission_db), "capture_mode": "public-transcript",
            "transcript_adapter": str(HERE / "dsh_transcript.py"), "redactor_executable": _redactor(profile),
            "summary_command": summary_cmd, "community_config": str(community_path),
            "feeds": prev_engine.get("feeds") if isinstance(prev_engine.get("feeds"), list) and prev_engine["feeds"] else [{"repository": f"mindie-agent/knowledge-{domain}", "ref": "main", "domain": domain, "interval_seconds": 300}],
        })
        if Path(str(old_canonical.get("engine_config") or "")).resolve() != engine_path.resolve(): _write(engine_path, engine_doc)
        os.environ["MINDIE_KNOWLEDGE_ROOT"] = str(root)
        res = ensure_knowledge_binding(profile, plugin_root=PLUGIN_ROOT, python_executable=python, install_redactor=False, retire_previous=True, already_locked=True)
        if res.get("status") != "configured":
            raise RuntimeError(f"knowledge binding {res.get('status')}: {res.get('reason')}")
        active_ep, domain_configs = _with_summary_command(profile, res, summary_cmd)
        _write(engine_path, engine_doc)
        binding_fields = dict(res.get("binding_fields") or {}, engine_config=active_ep, domain_configs=domain_configs)
        adapter = {
            **prev_cfg, **binding_fields, "schema": "mindie-dsh-adapter/1", "host": HARNESS, "version": VERSION, "python": python,
            "venv_dir": str(Path(python).parent.parent), "state_dir": str(profile.state_dir), "config_path": str(profile.config_path),
            "package_dir": str(PLUGIN_ROOT), "source_root": str(PLUGIN_ROOT), "runtime_scripts": str(HERE), "domain": domain, "knowledge_root": str(root),
        }
        if rebound is not None: adapter["component_commands"] = rebound
        _write(profile.config_path, adapter)
        if legacy_cfg_path != profile.config_path and legacy_cfg_path.is_file(): _write(legacy_cfg_path, adapter)
    finally:
        if prev_env_root is None: os.environ.pop("MINDIE_KNOWLEDGE_ROOT", None)
        else: os.environ["MINDIE_KNOWLEDGE_ROOT"] = prev_env_root
        lock.release()
    active_engine = _read(Path(active_ep)) or engine_doc
    return _emit({
        "status": "ready", "version": VERSION, "config_path": str(profile.config_path), "engine_config": active_ep,
        "python": python, "domain": domain, "transcript_adapter": active_engine.get("transcript_adapter", engine_doc["transcript_adapter"]),
        "summary_command": active_engine.get("summary_command", summary_cmd), "gc": maintain_runtime(profile),
    })

def _sharing(profile, argv: list[str]) -> int:
    from mindie_harness.sharing import configure_sharing, sharing_status
    parser = argparse.ArgumentParser(prog="setup.py sharing")
    group = parser.add_mutually_exclusive_group()
    for flag in ("--status", "--enable", "--local-only", "--disable"): group.add_argument(flag, action="store_true")
    parser.add_argument("--project-root", action="append", default=[])
    for opt in ("--repository", "--fork", "--account"): parser.add_argument(opt)
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
    command += (["--probe-remote"] if opts.probe_remote else []) + (["--cwd", opts.cwd] if opts.cwd else []) + (["--timeout", str(opts.timeout)] if opts.timeout is not None else [])
    return run_host(command)

def _update(profile, argv: list[str]) -> int:
    from mindie_harness.runtime.wheelhouse import update_runtime
    parser = argparse.ArgumentParser(prog="setup.py update")
    for flag in ("--force", "--offline", "--background"): parser.add_argument(flag, action="store_true")
    parser.add_argument("--channel-base-url")
    opts = parser.parse_args(argv)
    return _emit(update_runtime(profile, force=opts.force, offline=opts.offline, channel_base_url=opts.channel_base_url, background=opts.background))

def _declares(path: Path, parts: list[str]) -> bool:
    body = ast.parse(path.read_text(encoding="utf-8")).body
    for part in parts:
        node = next((item for item in body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and item.name == part), None)
        if node is None: return False
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
    if problems: raise SystemExit("unresolved release metadata targets: " + "; ".join(sorted(set(problems))))
    return _emit(validate_release_metadata({"schema": "mindie-plugin-release/1", "version": VERSION, "source": {"repository": REPOSITORY, "sha": opts.revision}, "compatibility": declaration}, repository=REPOSITORY, harness_name=HARNESS))

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
            boot = subprocess.run([sys.executable, "-I", "-X", "utf8", str(seed), "--host", HARNESS, "--plugin-root", str(PLUGIN_ROOT), "--config", str(stage_cfg)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
            if boot.returncode != 0: return boot.returncode
            staged_py = _read(stage_cfg).get("python")
            py = Path(staged_py) if isinstance(staged_py, str) and Path(staged_py).is_file() and not _is_win_store_stub(Path(staged_py)) else None
        finally: stage_cfg.unlink(missing_ok=True)
    if py is None or _same_interp(py, sys.executable):
        raise RuntimeError("MindIE seed bootstrap did not yield a modern runtime interpreter")
    return subprocess.run([str(py), "-I", "-X", "utf8", str(Path(__file__).resolve()), *raw_args], creationflags=CREATE_NO_WINDOW).returncode

def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    args, config = list(raw_args), None
    while "--config" in args:
        index = args.index("--config")
        if index + 1 >= len(args): raise SystemExit("--config requires a path")
        config, args = args[index + 1], args[:index] + args[index + 2:]
    command = args[0] if args and args[0] in COMMANDS else "init"
    rest = args[1:] if args and args[0] in COMMANDS else args
    if command == "release-metadata":
        return _release(rest) if importlib.util.find_spec("mindie_knowledge") is not None else _bootstrap_legacy_seed(raw_args, config)
    if not _has_modern_harness():
        return _bootstrap_legacy_seed(raw_args, config)
    profile = _profile(config)
    return _init(profile, rest) if command in {"init", "install"} else {"sharing": _sharing, "doctor": _doctor, "update": _update}[command](profile, rest)

if __name__ == "__main__":
    raise SystemExit(main())
