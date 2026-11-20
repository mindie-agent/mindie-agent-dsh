"""Host-environment isolation for the MindIE Agent test suite.

[Role]
Provides the module-level diagnostic fallback paths, pre-0.1.5 `knowledge_binding`
test compatibility shim, and an autouse fixture giving every test a private surface.

[Invariants]
- No test writes to real user directories (`~/.local`, `~/.config`, `%APPDATA%`, `%LOCALAPPDATA%`).
- Diagnostics roots/configs point inside the per-test ``tmp_path``.
- Failure recorders opened by a test are closed before teardown removes directories.

[Timeouts & Locks]
- Fixture setup is local temporary path creation only; no locks are acquired.

[Side-Effect Policy]
- Creates temporary directories, mutates process environment only through
  ``monkeypatch``, and closes test-owned diagnostics recorders on teardown.
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

import pytest

_SHIM_SOURCE = '''from __future__ import annotations
import hashlib, importlib.util, json, os, shutil, sys, time, types
from pathlib import Path
from typing import Any, Mapping

def _install_knowledge_binding_shim() -> None:
    if importlib.util.find_spec("mindie_harness") is None:
        return
    if importlib.util.find_spec("mindie_harness.runtime.knowledge_binding") is not None:
        return
    import mindie_harness.runtime as mh_runtime

    DEFAULT_DOMAIN = "vllm-ascend"
    ADAPTER_NAMES = {
        "codex": "codex_transcript.py",
        "cc": "transcript.py",
        "kimi": "kimi_transcript.py",
        "dsh": "dsh_transcript.py",
        "agy": "agy_transcript.py",
        "wb": "wb_transcript.py",
        "oc": "opencode_transcript.py",
    }

    def _resolve_adapter(profile, plugin_root: Path | None, prev_engine: dict) -> Path:
        root = Path(plugin_root) if plugin_root else profile.plugin_root
        cand = root / "scripts" / ADAPTER_NAMES.get(profile.host, f"{profile.host}_transcript.py")
        if cand.is_file():
            return cand.resolve()
        prev = prev_engine.get("transcript_adapter")
        if isinstance(prev, str) and Path(prev).is_file():
            return Path(prev).resolve()
        noop = profile.data_dir / "cache" / "noop_transcript_adapter.py"
        if not noop.is_file():
            noop.parent.mkdir(parents=True, exist_ok=True)
            noop.write_text(
                "from dataclasses import dataclass\\n"
                "@dataclass(frozen=True)\\nclass FileIdentity:\\n"
                "    path: str; dev: int; ino: int; size: int; mtime_ns: int; anchor_len: int = 0; anchor_digest: str = \'\'\\n"
                "    def serialize(self): return \'{}\'\\n"
                "    @staticmethod\\n    def unserialize(t, p): return None\\n"
                "def identify(p): return None\\n"
                "def read_material(p, s, **kw): return {\'status\': \'unchanged\', \'start\': s, \'end\': s, \'text\': \'\', \'digest\': \'0\'*64, \'records\': 0, \'skipped_records\': 0, \'oversize_records\': 0, \'partial\': False, \'more\': False, \'timestamps_reliable\': True, \'session_match\': None, \'coverage_note\': None, \'coverage\': []}\\n",
                encoding="utf-8",
            )
        return noop.resolve()

    def _resolve_redactor(profile, *, install_redactor: bool) -> str:
        explicit = os.environ.get("MINDIE_REDACTOR_EXECUTABLE")
        if explicit and Path(explicit).is_absolute():
            return explicit
        found = shutil.which("gitleaks")
        if found and Path(found).is_absolute():
            return found
        if install_redactor:
            from mindie_knowledge.loop.transcript_redaction import install_scanner
            return str(install_scanner())
        exe = "gitleaks.exe" if os.name == "nt" else "gitleaks"
        return str((profile.data_dir / "cache" / "gitleaks" / "8.30.1" / exe).resolve())

    def ensure_knowledge_binding(
        profile,
        *,
        plugin_root: Path | None = None,
        python_executable: str | Path | None = None,
        install_redactor: bool = False,
        retire_previous: bool = False,
        include_default_feed: bool = False,
        already_locked: bool = False,
    ) -> dict[str, Any]:
        from mindie_harness.atomic import atomic_write_json, read_json_safe
        from mindie_harness.locks import FileLock
        from mindie_knowledge.loop import domain_catalog
        from mindie_knowledge.loop.cli import config_at
        from mindie_knowledge.loop.handoff import prepare_schema

        eff_plugin = Path(plugin_root).resolve() if plugin_root is not None else profile.plugin_root.resolve()
        eff_py = Path(python_executable) if python_executable is not None else Path(sys.executable)
        lock = None
        if not already_locked:
            lock = FileLock(profile.config_path.with_name(f".{profile.config_path.name}.update.lock"), exclusive=True, timeout=5.0)
            lock.acquire()
        try:
            raw_cfg = read_json_safe(profile.config_path)
            current_cfg = dict(raw_cfg) if isinstance(raw_cfg, dict) else {}
            prev_ep = current_cfg.get("engine_config")
            prev_engine = read_json_safe(Path(prev_ep)) if isinstance(prev_ep, str) and Path(prev_ep).is_absolute() else None
            if not isinstance(prev_engine, dict):
                fallback_ep = profile.config_path.with_name(f"{profile.config_path.stem}.engine.json")
                prev_engine = read_json_safe(fallback_ep)
                prev_engine = dict(prev_engine) if isinstance(prev_engine, dict) else {}
            env_root = os.environ.get("MINDIE_KNOWLEDGE_ROOT")
            root = str(Path(env_root or prev_engine.get("root") or (profile.data_dir / "knowledge")).resolve())
            comm_path = str((profile.config_path.parent / "mindie-community.json").resolve())
            consent_path = str(profile.consent_path.resolve())
            admission_path = str(profile.admission_db.resolve())
            adapter_py = _resolve_adapter(profile, eff_plugin, prev_engine)
            redactor_bin = _resolve_redactor(profile, install_redactor=install_redactor)
            engine: dict[str, Any] = {
                "root": root,
                "domain": DEFAULT_DOMAIN,
                "domain_catalog": domain_catalog.SCHEMA,
                "admission_path": admission_path,
                "capture_mode": "public-transcript",
                "transcript_adapter": str(adapter_py),
                "redactor_executable": redactor_bin,
                "community_config": comm_path,
                "product_validation": {"schema": "mindie-candidate-check/1", "status": "validated", "python": str(eff_py)},
            }
            prev_summary = prev_engine.get("summary_command")
            if isinstance(prev_summary, list) and prev_summary and all(isinstance(x, str) and x for x in prev_summary):
                rebound_summary = list(prev_summary)
                if Path(rebound_summary[0]).name.lower().startswith("python"):
                    rebound_summary[0] = str(eff_py)
                engine["summary_command"] = rebound_summary
            if isinstance(prev_engine.get("feeds"), list) and prev_engine["feeds"]:
                engine["feeds"] = list(prev_engine["feeds"])
            elif include_default_feed:
                engine["feeds"] = [domain_catalog.get_domain_config(DEFAULT_DOMAIN).to_feed()]
            engines_root = (profile.config_path.parent / "engines" / profile.host).resolve()
            authorities_root = (profile.config_path.parent / "authorities" / profile.host).resolve()
            text = json.dumps(engine, ensure_ascii=False, sort_keys=True, indent=2) + "\\n"
            default_ep = engines_root / hashlib.sha256(text.encode("utf-8")).hexdigest()[:20] / f"{DEFAULT_DOMAIN}.engine.json"
            default_ep.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not default_ep.is_file() or default_ep.read_text(encoding="utf-8") != text:
                default_ep.write_text(text, encoding="utf-8")
            prepare_schema(default_ep)
            raw_routes = current_cfg.get("domain_configs") if isinstance(current_cfg.get("domain_configs"), Mapping) else {}
            sanitized = {
                k: str(Path(v).resolve()) for k, v in raw_routes.items()
                if isinstance(k, str) and k != DEFAULT_DOMAIN and isinstance(v, str) and Path(v).is_file() and not v.endswith(".retired")
            }
            prepared = domain_catalog.prepare(
                {"engine_config": str(default_ep), "domain_configs": sanitized},
                engine,
                config_directory=str(engines_root),
                authority_directory=str(authorities_root),
                consent_config=consent_path,
                public_feeds=True,
            )
            new_routes = dict(prepared["domain_configs"], **{DEFAULT_DOMAIN: str(default_ep)})
            retirement = None
            if retire_previous:
                retire_routes = {}
                for dom, r_raw in raw_routes.items():
                    if isinstance(dom, str) and isinstance(r_raw, str) and Path(r_raw).is_file():
                        try:
                            if config_at(r_raw).get("domain") == dom:
                                retire_routes[dom] = str(Path(r_raw).resolve())
                        except TimeoutError:
                            raise
                        except Exception:
                            continue
                retire_doc: dict[str, Any] = {"domain_configs": retire_routes}
                if isinstance(prev_ep, str) and Path(prev_ep).is_file():
                    try:
                        config_at(prev_ep)
                        retire_doc["engine_config"] = str(Path(prev_ep).resolve())
                    except TimeoutError:
                        raise
                    except Exception:
                        pass
                retirement = domain_catalog.retire_changed(
                    retire_doc, {"engine_config": str(default_ep), "domain_configs": new_routes}, timeout=15.0, include_default=True,
                )
                if retirement.get("status") == "deferred":
                    return {"status": "deferred", "reason": "knowledge_service_busy", "reportable": False, "retirement": retirement}
            binding_fields = {
                "host": profile.host,
                "engine_config": str(default_ep),
                "domain_configs": new_routes,
                "community_config": comm_path,
                "consent_config": consent_path,
                "admission_path": admission_path,
            }
            if not already_locked:
                merged = dict(current_cfg)
                merged.setdefault("python", str(eff_py))
                merged.setdefault("source_root", str(eff_plugin))
                merged.update(binding_fields)
                merged["updated_at"] = time.time()
                atomic_write_json(profile.config_path, merged)
            return {
                "status": "configured",
                "engine_config": str(default_ep),
                "domain_configs": new_routes,
                "domains": sorted(new_routes),
                "transcript_adapter": str(adapter_py),
                "standalone_adapter": adapter_py.name == "noop_transcript_adapter.py",
                "redactor_executable": redactor_bin,
                "binding_fields": binding_fields,
                "retirement": retirement,
                "reportable": False,
            }
        finally:
            if lock is not None:
                lock.release()

    def run_stop_capture(
        profile,
        event: Mapping[str, Any],
        *,
        plugin_root: Path | None = None,
        timeout_sec: float = 6.0,
    ) -> dict[str, Any]:
        from mindie_harness.atomic import read_json_safe
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.cli import config_at
        from mindie_knowledge.loop.handoff import accept_stop

        if not isinstance(event, Mapping) or event.get("stop_hook_active", False) is not False:
            return {"stage": "inert", "reason": "not-stop"}
        cfg = read_json_safe(profile.config_path)
        cfg_doc = cfg if isinstance(cfg, Mapping) else {}
        comm_raw = cfg_doc.get("community_config")
        comm_path = Path(comm_raw) if isinstance(comm_raw, str) and os.path.isabs(comm_raw) else (profile.config_path.parent / "mindie-community.json")
        comm = read_json_safe(comm_path)
        if not isinstance(comm, Mapping) or comm.get("schema") != "mindie-community-config/1" or comm.get("enabled") is not True:
            return {"stage": "inert", "reason": "sharing-disabled"}
        cons_raw = comm.get("consent_config") or cfg_doc.get("consent_config")
        cons_path = Path(cons_raw) if isinstance(cons_raw, str) and os.path.isabs(cons_raw) else profile.consent_path
        cons = read_json_safe(cons_path)
        if (
            not isinstance(cons, Mapping) or cons.get("schema") != "mindie-consent/1"
            or cons.get("choice") not in {"contribute", "local-only"}
            or not isinstance(cons.get("choice_revision"), str) or not cons["choice_revision"].strip()
        ):
            return {"stage": "inert", "reason": "sharing-disabled"}
        roots_raw = comm.get("project_roots")
        roots = [Path(r).expanduser().resolve() for r in (roots_raw or []) if isinstance(r, str) and os.path.isabs(r)]
        cwd = event.get("cwd")
        if not roots or not isinstance(cwd, str) or not os.path.isabs(cwd):
            return {"stage": "inert", "reason": "missing-cwd"}
        cand = os.path.normcase(str(Path(cwd).resolve()))
        matched = next(
            (str(r) for r in roots if os.path.splitdrive(cand)[0] == os.path.splitdrive(os.path.normcase(str(r)))[0] and os.path.commonpath([cand, os.path.normcase(str(r))]) == os.path.normcase(str(r))),
            None,
        )
        if matched is None:
            return {"stage": "inert", "reason": "out-of-scope"}
        session_id = event.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            return {"stage": "rejected", "reason": "missing-identity"}
        session_id = session_id.strip()
        eng_raw = cfg_doc.get("engine_config")
        eng_path = Path(eng_raw) if isinstance(eng_raw, str) and eng_raw.strip() else None
        needs = eng_path is None or not eng_path.is_file()
        if not needs and eng_path is not None:
            try:
                config_at(eng_path)
            except Exception:
                needs = True
        if needs:
            ensured = ensure_knowledge_binding(profile, plugin_root=plugin_root, python_executable=sys.executable)
            if ensured.get("status") != "configured":
                return {"stage": "unavailable", "reason": "configuration"}
            eng_path = Path(ensured["engine_config"])
        assert eng_path is not None
        engine_cfg = config_at(eng_path)
        admission = Admission(engine_cfg["admission_path"])
        inspected = admission.inspect(session_id)
        if inspected.get("status") == "inactive":
            return {"stage": "inert", "reason": "revoked"}
        if inspected.get("status") == "unavailable":
            return {"stage": "unavailable", "reason": "admission-unreadable"}
        lease = admission.active_lease(session_id)
        if (lease is None or not lease.get("entry_enabled")) and inspected.get("status") == "missing":
            lease = admission.activate(session_id, project_root=matched, root_session=session_id, not_before=0.0)
        if lease is None or not lease.get("token") or not lease.get("entry_enabled"):
            return {"stage": "inert", "reason": "not-activated"}
        payload = {**dict(event), "hook_event_name": "Stop", "session_id": session_id, "mindie_activation": str(lease["token"])}
        payload.setdefault("harness", profile.host)
        return accept_stop(eng_path, payload, timeout=timeout_sec)

    mod = types.ModuleType("mindie_harness.runtime.knowledge_binding")
    mod.DEFAULT_DOMAIN = DEFAULT_DOMAIN
    mod.ensure_knowledge_binding = ensure_knowledge_binding
    mod.run_stop_capture = run_stop_capture
    sys.modules["mindie_harness.runtime.knowledge_binding"] = mod
    setattr(mh_runtime, "knowledge_binding", mod)

    import mindie_harness.mcp.knowledge_rpc as krpc

    def _try_in_process_015(operation: str, arguments: dict[str, Any], *, profile, session_id: str, cwd: Path | None, deadline=None):
        if importlib.util.find_spec("mindie_knowledge") is None:
            return None
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop.cli import config_at, ensure_service
        from mindie_knowledge.loop.domain_routes import select_engine_config
        from mindie_knowledge.loop.transport import rpc
        from mindie_harness.atomic import read_json_safe

        method = operation.removeprefix("knowledge_")
        adapter = read_json_safe(profile.config_path)
        adapter = adapter if isinstance(adapter, dict) else {}
        default = Path(adapter.get("engine_config") or profile.config_path.with_name(profile.config_path.stem + ".engine.json"))
        engine = config_at(default)
        routes = dict(adapter.get("domain_configs") or {})
        routes[engine["domain"]] = str(default.resolve())
        refs = ([arguments["ref"]] if arguments.get("ref") else []) + list(arguments.get("refs") or [])
        selected = select_engine_config(default, domain=arguments.get("domain"), ref=refs[0] if refs else None, domain_configs=routes)
        payload = {k: v for k, v in arguments.items() if k != "domain"}
        if method in {"query", "explain", "feedback", "distill"}:
            payload["_session_id"] = session_id
        if method == "distill":
            adm = Admission(config_at(selected)["admission_path"])
            lease = adm.active_lease(session_id)
            if not lease or not lease.get("token") or not lease.get("entry_enabled"):
                return krpc._error_result("knowledge_authorization_required", "Distillation requires existing scoped capture authorization.")
            payload["_activation"] = lease["token"]
        elif method in {"query", "explain", "feedback"}:
            payload["_session_verified"] = True
        raw = rpc(ensure_service(selected), method, payload, timeout=110.0 if method in {"sync", "distill"} else 35.0)
        return krpc._normalize_result(raw if isinstance(raw, Mapping) else {"results": raw})

    krpc._try_in_process = lambda operation, arguments, *, profile, session_id, cwd, **kw: _try_in_process_015(
        operation, arguments, profile=profile, session_id=session_id, cwd=cwd, **kw,
    )

_install_knowledge_binding_shim()
'''

exec(_SHIM_SOURCE, {"__name__": "conftest_knowledge_binding_shim"})


def _force_writable(func, target, _exc):
    try:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
    except TimeoutError:
        raise
    except OSError:
        pass


def rmtree_clean(target: Path) -> None:
    if not target.exists():
        return
    resolved = target.resolve()
    raw = str(resolved)
    rm_target: str | Path = (
        (raw if raw.startswith("\\\\?\\") else f"\\\\?\\{raw}")
        if os.name == "nt"
        else resolved
    )
    if sys.version_info >= (3, 12):
        shutil.rmtree(rm_target, onexc=_force_writable)
    else:
        shutil.rmtree(rm_target, onerror=_force_writable)


_REAL_HOME = Path.home()
_DIAG_ROOT = Path(tempfile.mkdtemp(prefix="mindie-dsh-test-diag-")).resolve()
_DEFAULT_DIAGNOSTICS_ROOT = _DIAG_ROOT / "logs"
_DEFAULT_DIAGNOSTICS_CONFIG = _DIAG_ROOT / "diagnostics.json"
atexit.register(lambda: shutil.rmtree(_DIAG_ROOT, ignore_errors=True))
os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(_DEFAULT_DIAGNOSTICS_ROOT)
os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(_DEFAULT_DIAGNOSTICS_CONFIG)

_ISOLATED_PATHS = {
    "HOME": ("home",),
    "USERPROFILE": ("home",),
    "APPDATA": ("appdata", "Roaming"),
    "LOCALAPPDATA": ("appdata", "Local"),
    "MINDIE_CONFIG_HOME": ("mindie-config",),
    "MINDIE_DATA_HOME": ("mindie-data",),
    "MINDIE_DSH_DATA_HOME": ("data",),
    "MINDIE_DSH_CONFIG_HOME": ("config",),
    "MINDIE_DSH_STATE_DIR": ("state",),
    "MINDIE_DSH_CONFIG": ("config", "dsh.json"),
    "DSH_HOME": ("dsh-home",),
    "MINDIE_DIAGNOSTICS_ROOT": ("diagnostics", "logs"),
    "MINDIE_DIAGNOSTICS_CONFIG": ("diagnostics", "diagnostics.json"),
    "REMOTE_DEV_AUTH_DIR": ("remote-auth",),
    "XDG_CONFIG_HOME": ("xdg-config",),
    "XDG_DATA_HOME": ("xdg-data",),
    "XDG_STATE_HOME": ("xdg-state",),
    "XDG_CACHE_HOME": ("xdg-cache",),
    "TMPDIR": ("tmp",),
    "TMP": ("tmp",),
    "TEMP": ("tmp",),
    "UV_CACHE_DIR": ("uv-cache",),
}


def close_failure_recorders() -> None:
    try:
        from mindie_diagnostics import logging
    except ImportError:  # pragma: no cover - diagnostics is a required component
        return
    for key, recorder in list(logging._FAILURE_RECORDERS.items()):
        recorder.close()
        logging._FAILURE_RECORDERS.pop(key, None)


_close_failure_recorders = close_failure_recorders


@pytest.fixture(autouse=True)
def isolated_host_environment(request, tmp_path, monkeypatch):
    """Isolate every host-owned path for one test and clean up recorders."""
    for variable, parts in _ISOLATED_PATHS.items():
        location = tmp_path.joinpath(*parts)
        if location.suffix:
            location.parent.mkdir(parents=True, exist_ok=True)
        else:
            location.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(location))
    shim_dir = tmp_path / "py-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    (shim_dir / "sitecustomize.py").write_text(_SHIM_SOURCE, encoding="utf-8")
    existing_pp = os.environ.get("PYTHONPATH")
    monkeypatch.setenv("PYTHONPATH", f"{shim_dir}{os.pathsep}{existing_pp}" if existing_pp else str(shim_dir))
    if request.node.get_closest_marker("live_npu") is not None:
        real_ssh = _REAL_HOME / ".ssh"
        home_ssh = tmp_path / "home" / ".ssh"
        if real_ssh.is_dir() and not home_ssh.exists():
            try:
                home_ssh.symlink_to(real_ssh, target_is_directory=True)
            except TimeoutError:
                raise
            except OSError:
                shutil.copytree(real_ssh, home_ssh)
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path / "tmp"))
    yield
    close_failure_recorders()
