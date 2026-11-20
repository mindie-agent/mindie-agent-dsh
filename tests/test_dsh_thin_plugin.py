"""Thin DSH plugin contract: seed, hooks, MCP surfaces, setup, compatibility.

[Role]
Verifies the bundled seed lock, cold-seed MCP stdout purity, the `<100ms`
SQLite-free `Stop` gate, `local-only`/`contribute` authorization parity,
explicit `Admission` revocation & migration across `admission_path`, the 14-tool
`remote-dev` plus `mindie-tools` surfaces, and `setup.py` single-pass binding.

[Invariants]
- Every subprocess uses the isolated environment from `conftest.py`.
- No test writes outside its `tmp_path` or leaves a knowledge service running.

[Timeouts & Locks]
- Fast-path timing is measured in-process; setup, MCP, and release subprocesses
  are bounded at 90s. The knowledge service is stopped in `finally`.

[Side-Effect Policy]
- Writes only test-owned configs, SQLite stores, and process stdout.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SESSION = "11111111-2222-3333-4444-555555555555"
NO_WIN = 0x08000000 if os.name == "nt" else 0
REMOTE_TOOLS = {
    "remote_read", "remote_write", "remote_edit", "remote_bash", "remote_glob",
    "remote_grep", "remote_job_output", "remote_job_list", "remote_job_kill",
    "remote_read_image", "remote_auth", "remote_probe", "remote_artifact_pull",
    "remote_artifact_push",
}
KNOWLEDGE_TOOLS = {
    "knowledge_query", "knowledge_explain", "knowledge_feedback",
    "knowledge_distill", "knowledge_sync",
}


def _load_launch():
    spec = importlib.util.spec_from_file_location("dsh_launch_under_test", SCRIPTS / "mindie_launch.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, value: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class _Stream:
    def __init__(self, data: bytes = b"") -> None:
        self.buffer = io.BytesIO(data)


def _run(monkeypatch, launch, argv: list[str], stdin: bytes = b"") -> tuple[int, bytes, float]:
    stdout = _Stream()
    monkeypatch.setattr(sys, "argv", ["mindie_launch.py", *argv])
    monkeypatch.setattr(sys, "stdin", _Stream(stdin))
    monkeypatch.setattr(sys, "stdout", stdout)
    started = time.perf_counter()
    code = launch.main()
    return code, stdout.buffer.getvalue(), time.perf_counter() - started


def _authorize(tmp_path: Path, root: Path, choice: str = "contribute") -> Path:
    cfg = Path(os.environ["MINDIE_DSH_CONFIG"])
    engine_path = cfg.with_name("dsh.engine.json")
    comm_path = cfg.parent / "mindie-community.json"
    cons_path = cfg.parent / "mindie-consent.json"
    _write(cfg, {
        "python": sys.executable,
        "engine_config": str(engine_path),
        "community_config": str(comm_path),
        "consent_config": str(cons_path),
    })
    _write(engine_path, {
        "root": str(tmp_path / "knowledge"),
        "domain": "vllm-ascend",
        "admission_path": str(tmp_path / "admission.sqlite3"),
        "capture_mode": "public-transcript",
        "transcript_adapter": str(SCRIPTS / "dsh_transcript.py"),
        "redactor_executable": sys.executable,
        "community_config": str(comm_path),
    })
    _write(comm_path, {
        "schema": "mindie-community-config/1",
        "enabled": choice in {"contribute", "local-only"},
        "project_roots": [str(root)],
        "consent_config": str(cons_path),
    })
    _write(cons_path, {
        "schema": "mindie-consent/1",
        "choice": choice,
        "choice_revision": "test-rev-1",
        "enabled": choice in {"contribute", "local-only"},
        "project_roots": [str(root)],
    })
    return cfg


def _no_sqlite(root: Path) -> bool:
    return not list(root.rglob("*.sqlite*"))


def test_seed_lock_verification(tmp_path: Path) -> None:
    launch = _load_launch()
    seed = ROOT / "assets" / "mindie-harness-seed.pyz"
    lock = ROOT / "assets" / "seed-lock.json"
    document = launch._verify_seed(seed, lock)
    assert document["schema"] == "mindie-harness-seed/1"
    assert document["size_bytes"] > 0 and len(document["sha256"]) == 64
    forged = tmp_path / "seed-lock.json"
    _write(forged, dict(document, sha256="0" * 64))
    with pytest.raises(ValueError):
        launch._verify_seed(seed, forged)


def test_cold_mcp_seed_bootstrap_keeps_stdout_pure_jsonrpc(monkeypatch, tmp_path: Path) -> None:
    launch = _load_launch()
    fake_plugin = tmp_path / "plugin"
    assets = fake_plugin / "assets"
    assets.mkdir(parents=True)
    seed_pyz = assets / "mindie-harness-seed.pyz"
    with zipfile.ZipFile(seed_pyz, "w") as zf:
        zf.writestr(
            "__main__.py",
            "import json, pathlib, sys\n"
            "cfg = pathlib.Path(sys.argv[sys.argv.index('--config') + 1])\n"
            "cfg.parent.mkdir(parents=True, exist_ok=True)\n"
            "cfg.write_text(json.dumps({'python': sys.executable}), encoding='utf-8')\n"
            "sys.stdout.write(json.dumps({'status': 'provisioned', 'python': sys.executable}) + '\\n')\n"
            "sys.stdout.flush()\n",
        )
    seed_bytes = seed_pyz.read_bytes()
    _write(assets / "seed-lock.json", {
        "schema": "mindie-harness-seed/1",
        "version": "0.1.8",
        "artifact": "mindie-harness-seed.pyz",
        "sha256": hashlib.sha256(seed_bytes).hexdigest(),
        "size_bytes": len(seed_bytes),
    })
    cfg_path = tmp_path / "cold-cfg" / "dsh.json"
    monkeypatch.setattr(launch, "PLUGIN_ROOT", fake_plugin)
    probe = subprocess.run(
        [
            sys.executable, "-c",
            (
                "import importlib.util, pathlib, sys\n"
                f"spec = importlib.util.spec_from_file_location('ml', {str(SCRIPTS / 'mindie_launch.py')!r})\n"
                "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
                f"m.PLUGIN_ROOT = pathlib.Path({str(fake_plugin)!r})\n"
                "it = iter([False, True])\n"
                "m._has_runtime = lambda: next(it)\n"
                "m._run_in_runtime = lambda argv, cfg, event=None: (sys.stdout.buffer.write(b'{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"tools\":[]}}\\n'), sys.stdout.buffer.flush(), 0)[-1]\n"
                f"raise SystemExit(m._bootstrap(['mcp', 'tools'], pathlib.Path({str(cfg_path)!r})))\n"
            ),
        ],
        capture_output=True,
        timeout=30,
        creationflags=NO_WIN,
    )
    assert probe.returncode == 0, probe.stderr.decode("utf-8", "replace")
    assert probe.stdout == b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n'
    assert b'"provisioned"' not in probe.stdout


def test_paths_canonical_and_legacy_precedence(tmp_path: Path) -> None:
    script = """
const path = require('node:path');
const mod = require(path.join(process.argv[1], 'paths.cjs'));
const p = mod.paths();
console.log(JSON.stringify({configFile: p.configFile, configHome: p.configHome,
  stateDir: p.stateDir, spawn: mod.backgroundSpawnOptions('win32')}));
"""
    base = tmp_path / "cfg"
    canonical = base / "dsh.json"
    legacy = base / "dsh" / "config.json"
    env = dict(os.environ, MINDIE_CONFIG_HOME=str(base), MINDIE_DATA_HOME=str(tmp_path / "data"))
    for name in ("MINDIE_DSH_CONFIG_HOME", "MINDIE_AGENT_CONFIG", "MINDIE_DSH_CONFIG", "MINDIE_DSH_STATE_DIR"):
        env.pop(name, None)
    _write(canonical, {"python": sys.executable})
    output = json.loads(subprocess.run(
        ["node", "-e", script, str(ROOT)], capture_output=True, text=True, env=env, timeout=30, check=True,
    ).stdout)
    assert output["configFile"] == str(canonical.resolve())
    assert output["configHome"] == str(canonical.parent.resolve())
    assert output["spawn"] == {"windowsHide": True, "detached": False}
    canonical.unlink()
    _write(legacy, {"python": sys.executable})
    output = json.loads(subprocess.run(
        ["node", "-e", script, str(ROOT)], capture_output=True, text=True, env=env, timeout=30, check=True,
    ).stdout)
    assert output["configFile"] == str(legacy.resolve())
    env["MINDIE_DSH_CONFIG_HOME"] = str(tmp_path / "legacy")
    _write(tmp_path / "legacy" / "config.json", {"python": sys.executable})
    output = json.loads(subprocess.run(
        ["node", "-e", script, str(ROOT)], capture_output=True, text=True, env=env, timeout=30, check=True,
    ).stdout)
    assert output["configFile"] == str((tmp_path / "legacy" / "config.json").resolve())
    env.pop("MINDIE_DSH_CONFIG_HOME")
    env["MINDIE_AGENT_CONFIG"] = str(tmp_path / "explicit.json")
    _write(Path(env["MINDIE_AGENT_CONFIG"]), {"python": sys.executable})
    output = json.loads(subprocess.run(
        ["node", "-e", script, str(ROOT)], capture_output=True, text=True, env=env, timeout=30, check=True,
    ).stdout)
    assert output["configFile"] == str(Path(env["MINDIE_AGENT_CONFIG"]).resolve())
    env.pop("MINDIE_AGENT_CONFIG")
    canonical.write_text("{not-json", encoding="utf-8")
    failed = subprocess.run(["node", "-e", script, str(ROOT)], capture_output=True, text=True, env=env, timeout=30)
    assert failed.returncode != 0


def test_stop_fast_paths_are_quiet_and_sqlite_free(monkeypatch, tmp_path: Path) -> None:
    launch = _load_launch()
    monkeypatch.setenv("MINDIE_DSH_CONFIG", str(tmp_path / "cfg" / "dsh.json"))
    code, output, elapsed = _run(monkeypatch, launch, ["hook", "stop"])
    assert (code, output) == (0, b"{}\n") and elapsed < 0.1
    assert _no_sqlite(tmp_path)
    root, sibling = tmp_path / "work" / "app", tmp_path / "work" / "app-other"
    root.mkdir(parents=True); sibling.mkdir(parents=True)
    _authorize(tmp_path, root, "disabled")
    code, output, elapsed = _run(monkeypatch, launch, ["hook", "stop"])
    assert (code, output) == (0, b"{}\n") and elapsed < 0.1
    _authorize(tmp_path, root, "local-only")
    event = json.dumps({"session_id": SESSION, "cwd": str(sibling), "turn_id": "1"}).encode()
    code, output, elapsed = _run(monkeypatch, launch, ["hook", "stop"], event)
    assert (code, output) == (0, b"{}\n") and elapsed < 0.1
    assert _no_sqlite(tmp_path)


@pytest.mark.parametrize("choice", ["local-only", "contribute"])
def test_stop_authorization_parity(monkeypatch, tmp_path: Path, choice: str) -> None:
    launch = _load_launch()
    root = tmp_path / "workspace"; root.mkdir()
    _authorize(tmp_path, root, choice)
    seen = []
    monkeypatch.setattr(launch, "_run_in_runtime", lambda argv, cfg, event: seen.append(event) or 0)
    event = {"session_id": SESSION, "cwd": str(root), "hook_event_name": "Stop", "identity_kind": "turn",
             "turn_id": "3", "transcript_path": str(root / "t.zstd"), "harness": "dsh"}
    code, _output, _ = _run(monkeypatch, launch, ["hook", "stop"], json.dumps(event).encode())
    assert code == 0
    assert seen == [event]


def test_explicit_admission_revocation_preserved(monkeypatch, tmp_path: Path) -> None:
    launch = _load_launch()
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    cfg = _authorize(tmp_path, workspace, "contribute")
    from mindie_harness.paths import ProfileRef
    from mindie_knowledge.loop.activation import Admission

    profile = ProfileRef.resolve("dsh", plugin_root=ROOT, config_path=cfg)
    engine_doc = json.loads(cfg.with_name("dsh.engine.json").read_text(encoding="utf-8"))
    engine_doc["admission_path"] = str(profile.admission_db)
    _write(cfg.with_name("dsh.engine.json"), engine_doc)
    admission = Admission(profile.admission_db)
    admission.activate(SESSION, project_root=str(workspace), not_before=0.0)
    admission.deactivate(SESSION)
    assert admission.inspect(SESSION)["status"] == "inactive"
    monkeypatch.setattr("mindie_knowledge.loop.handoff.accept_stop", lambda *a, **k: pytest.fail("capture must not run"))
    stdout = _Stream()
    monkeypatch.setattr(sys, "stdout", stdout)
    assert launch._stop_runtime(profile, {"session_id": SESSION, "cwd": str(workspace), "turn_id": "1"}) == 0
    assert stdout.buffer.getvalue() == b"{}\n"
    assert admission.inspect(SESSION)["status"] == "inactive"


def test_setup_init_preserves_revoked_sessions_across_admission_migration_and_retires_routes(monkeypatch, tmp_path: Path) -> None:
    from mindie_harness.paths import ProfileRef
    from mindie_harness.runtime.knowledge_binding import run_stop_capture
    from mindie_knowledge.loop.activation import Admission

    cfg_base = tmp_path / "legacy-revocation-cfg"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(cfg_base))
    for key in ("MINDIE_DSH_CONFIG", "MINDIE_AGENT_CONFIG", "MINDIE_DSH_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)

    legacy_adm = tmp_path / "custom-old-admission.sqlite3"
    old_adm = Admission(legacy_adm)
    old_adm.activate(SESSION, project_root=str(workspace), not_before=0.0)
    assert old_adm.claim(SESSION, "turn", "t-1") is True
    assert old_adm.deactivate(SESSION) is True

    legacy_dir = cfg_base / "dsh"
    legacy_dir.mkdir(parents=True)
    legacy_engine = legacy_dir / "engine.json"
    legacy_consent = legacy_dir / "mindie-consent.json"
    legacy_comm = legacy_dir / "mindie-community.json"
    _write(legacy_engine, {
        "root": str(tmp_path / "knowledge"),
        "domain": "vllm-ascend",
        "admission_path": str(legacy_adm),
        "capture_mode": "public-transcript",
        "transcript_adapter": str(SCRIPTS / "dsh_transcript.py"),
        "redactor_executable": sys.executable,
        "community_config": str(legacy_comm),
    })
    _write(legacy_consent, {
        "schema": "mindie-consent/1",
        "choice": "contribute",
        "choice_revision": "rev-mig-1",
        "enabled": True,
        "project_roots": [str(workspace)],
    })
    _write(legacy_comm, {
        "schema": "mindie-community-config/1",
        "enabled": True,
        "project_roots": [str(workspace)],
        "consent_config": str(legacy_consent),
    })
    _write(legacy_dir / "config.json", {
        "python": sys.executable,
        "engine_config": str(legacy_engine),
    })

    initialized = _setup("init")
    assert initialized["status"] == "ready"
    canonical_cfg = cfg_base / "dsh.json"
    profile = ProfileRef.resolve("dsh", plugin_root=ROOT, config_path=canonical_cfg)
    assert profile.admission_db.resolve() != legacy_adm.resolve()
    migrated = Admission(profile.admission_db)
    assert migrated.inspect(SESSION)["status"] == "inactive"
    outcome = run_stop_capture(profile, {"session_id": SESSION, "cwd": str(workspace), "turn_id": "2"}, plugin_root=ROOT)
    assert outcome == {"stage": "inert", "reason": "revoked"}

    # Re-running init when canonical dsh.json already has 9 domain_configs preserves routes into retire_changed
    reinit = _setup("init")
    assert reinit["status"] == "ready"
    assert len(json.loads(canonical_cfg.read_text(encoding="utf-8"))["domain_configs"]) == 9


def test_mcp_surfaces_are_separated_and_zero_meta(tmp_path: Path) -> None:
    launch = _load_launch()
    cfg = Path(os.environ["MINDIE_DSH_CONFIG"])
    engine_path = cfg.with_name("dsh.engine.json")
    _write(cfg, {"python": sys.executable, "engine_config": str(engine_path)})
    _write(engine_path, {
        "root": str(tmp_path / "knowledge"), "domain": "vllm-ascend",
        "admission_path": str(tmp_path / "admission.sqlite3"),
        "capture_mode": "public-transcript",
        "transcript_adapter": str(SCRIPTS / "dsh_transcript.py"),
        "redactor_executable": sys.executable,
        "community_config": str(tmp_path / "mindie-community.json"),
    })
    from mindie_harness.mcp.gateway import ManagedMCPAdapter
    from mindie_harness.api import serve_mcp
    from mindie_harness.paths import ProfileRef
    from mindie_knowledge.loop.handoff import prepare_schema

    profile = ProfileRef.resolve("dsh", plugin_root=ROOT, config_path=cfg)
    adapter = launch._dsh_adapter(plugin_root=ROOT, config_path=cfg)
    assert type(adapter) is ManagedMCPAdapter
    assert {tool["name"] for tool in adapter.mcp_tools("remote", profile)} == REMOTE_TOOLS
    tools = {tool["name"] for tool in adapter.mcp_tools("tools", profile)}
    assert KNOWLEDGE_TOOLS | {"mindie_doctor", "mindie_status", "profiler", "sim", "coordinator", "debugger", "npu_top"} <= tools
    prepare_schema(str(engine_path))
    stdin = io.BytesIO(b"\n".join(json.dumps(item).encode() for item in [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "knowledge_query", "arguments": {"query": "Ascend NPU", "request_nonce": "stale"},
            "_meta": {"session_id": SESSION},
        }},
    ]) + b"\n")
    stdout = io.BytesIO()
    try:
        assert serve_mcp("tools", profile=profile, adapter=adapter, stdin=stdin, stdout=stdout) == 0
    finally:
        try:
            from mindie_knowledge.loop.cli import connect
            from mindie_knowledge.loop.transport import rpc
            rpc(connect(json.loads(engine_path.read_text(encoding="utf-8"))), "stop", timeout=5.0)
        except Exception:
            pass
    responses = {message["id"]: message for message in map(json.loads, stdout.getvalue().splitlines())}
    assert "_meta" not in stdout.getvalue().decode("utf-8")
    assert "request_nonce" not in stdout.getvalue().decode("utf-8")
    assert responses[2]["result"]["isError"] is False
    assert "results" in responses[2]["result"]["content"][0]["text"]


def test_release_metadata_and_compatibility_registry() -> None:
    compatibility = json.loads((ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
    assert compatibility["schema"] == "mindie-harness-compatibility/1"
    assert compatibility["harness"] == "dsh"
    assert set(compatibility["used_interfaces"]) == set(compatibility["interface_registry"])
    for entry in compatibility["interface_registry"].values():
        for reference in entry["code_paths"]:
            assert (ROOT / reference.split(":", 1)[0]).is_file(), reference
        for reference in entry["test_ids"]:
            assert (ROOT / reference.split("::", 1)[0]).is_file(), reference
        assert entry["evidence_refs"] and (ROOT / entry["evidence_refs"][0]).is_file()
    contract = json.loads((ROOT / "product-contract.json").read_text(encoding="utf-8"))
    assert contract["schema"] == "mindie-product-contract/1"
    assert contract["validation"] == "mindie-candidate-check/1"
    assert contract["harness"] == "dsh"
    assert contract["native_version"] == "0.1.28"
    assert contract["components"]["npu-top"] == "0.3.5"
    assert contract["components"]["mindie-harness"] == "0.1.8"
    assert len(contract["components"]) == 9


def _setup(*args: str) -> dict:
    env = dict(os.environ, MINDIE_REDACTOR_EXECUTABLE=sys.executable)
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), *args],
        capture_output=True, text=True, timeout=90, creationflags=NO_WIN, env=env,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_setup_init_sharing_and_compatibility(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"; workspace.mkdir()
    initialized = _setup("init")
    assert initialized["status"] == "ready"
    assert initialized["version"] == "0.1.28"
    engine = json.loads(Path(initialized["engine_config"]).read_text(encoding="utf-8"))
    assert engine["domain"] == "vllm-ascend"
    assert engine["transcript_adapter"].endswith("dsh_transcript.py")
    assert engine["summary_command"][-1] == "summary"
    saved_cfg = json.loads(Path(initialized["config_path"]).read_text(encoding="utf-8"))
    assert len(saved_cfg.get("domain_configs") or {}) == 9
    assert initialized["gc"]["gc"]["status"] in {"completed", "deferred_in_use"}
    local = _setup("sharing", "--local-only", "--project-root", str(workspace))
    assert local["choice"] == "local-only" and local["enabled"] is True
    assert _setup("sharing", "--status")["tier"] == "local-only"
    assert _setup("sharing", "--enable", "--project-root", str(workspace))["choice"] == "contribute"
    assert _setup("sharing", "--disable")["choice"] == "disabled"
    conflict = subprocess.run(
        [sys.executable, str(SCRIPTS / "setup.py"), "sharing", "--enable", "--disable"],
        capture_output=True, text=True, timeout=30, creationflags=NO_WIN,
    )
    assert conflict.returncode == 2
    release = _setup("release-metadata", "--source", str(ROOT), "--tests", str(ROOT / "tests"),
                     "--revision", "a" * 40)
    assert release["schema"] == "mindie-plugin-release/1"
    assert release["version"] == "0.1.28"
    assert release["compatibility"]["harness"] == "dsh"
    compatibility = json.loads((ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
    assert compatibility["schema"] == "mindie-harness-compatibility/1"
    assert compatibility["supported"][0]["minimum"] == "0.2.0-rc.2"
    for entry in compatibility["interface_registry"].values():
        for reference in entry["code_paths"]:
            assert (ROOT / reference.split(":", 1)[0]).is_file(), reference
        for reference in entry["test_ids"]:
            assert (ROOT / reference.split("::", 1)[0]).is_file(), reference
