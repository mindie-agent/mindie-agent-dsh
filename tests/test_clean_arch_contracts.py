"""Orthogonal clean-architecture behavioral contracts for `mindie-agent-dsh`.

[Role]
Verifies `mindie-harness` seed lock integrity, `mindie_harness.rsi` delegation,
concurrent resident `component_mcp` execution (`AD-03`), symlink-friendly bounded
reads (`AD-07`), zero benchmark spillover (`AD-10`), and `v0.1.22` config/pointer
upgrade compatibility.

[Invariants]
- All file/process state stays isolated under `tmp_path` via `conftest.py`.
- Zero `MagicMock` hardware payloads; exercises real pipes, symlinks, and modules.

[Timeouts & Locks]
- Concurrent MCP test bounded under 8s; subprocess checks bounded under 15s.

[Side-Effect Policy]
- Writes only inside `tmp_path`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import component_mcp  # noqa: E402
import dsh_diagnostics  # noqa: E402
import mindie_launch  # noqa: E402


def test_seed_archive_matches_seed_lock_and_package_files() -> None:
    seed_pyz = ROOT / "assets" / "mindie-harness-seed.pyz"
    seed_lock = ROOT / "assets" / "seed-lock.json"
    assert seed_pyz.is_file()
    assert seed_lock.is_file()
    mindie_launch._verify_seed(seed_pyz, seed_lock)
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    assert "assets/" in package["files"]


def test_rsi_modules_delegate_to_mindie_harness_rsi() -> None:
    import experiment_ledger
    import rsi_explore
    import rsi_loop
    from mindie_harness import rsi as harness_rsi

    assert rsi_loop.__name__ == harness_rsi.rsi_loop.__name__
    assert experiment_ledger.__name__ == harness_rsi.experiment_ledger.__name__
    assert rsi_explore.schema_properties() == harness_rsi.rsi_explore.schema_properties()
    parsed = rsi_explore.parse_args({"mode": "explore", "goal": "latency"})
    assert parsed["mode"] == "explore"
    assert parsed["goal"] == "latency"


def test_concurrent_component_mcp_never_blocks_fast_status_behind_slow_call(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "python": sys.executable,
                "version": "0.1.22",
                "engine_config": str(tmp_path / "engine.json"),
                "component_commands": {
                    "sim": [sys.executable, "-c", "import time; time.sleep(0.35); print('{\"ok\":true}')"],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINDIE_DSH_CONFIG", str(config))

    r_in, w_in = os.pipe()
    r_out, w_out = os.pipe()
    reader = os.fdopen(r_in, "r", encoding="utf-8")
    client_in = os.fdopen(w_in, "w", encoding="utf-8")
    client_out = os.fdopen(r_out, "r", encoding="utf-8")
    writer = os.fdopen(w_out, "w", encoding="utf-8")
    server = threading.Thread(target=component_mcp.serve, args=(reader, writer), daemon=True)
    server.start()
    try:
        # Fire slow call (id=10) and fast status call (id=11) back-to-back without waiting
        client_in.write(json.dumps({"id": 10, "method": "tools/call", "params": {"name": "sim", "arguments": {"argv": []}}}) + "\n")
        client_in.write(json.dumps({"id": 11, "method": "tools/call", "params": {"name": "mindie_status", "arguments": {}}}) + "\n")
        client_in.flush()

        first = json.loads(client_out.readline())
        second = json.loads(client_out.readline())
        by_id = {first["id"]: first, second["id"]: second}
        assert set(by_id) == {10, 11}
        assert "error" not in by_id[10], by_id[10]
        assert "error" not in by_id[11], by_id[11]
        assert not by_id[10]["result"]["isError"]
        assert by_id[11]["result"]["structuredContent"]["status"] == "configured"
        # Fast status (id=11) must finish before the 350ms slow call (id=10)
        assert first["id"] == 11
    finally:
        client_in.close()
        server.join(timeout=5.0)
        for s in (reader, writer, client_out):
            try:
                s.close()
            except OSError:
                pass


def test_diagnostics_bounded_reads_accept_legitimate_symlinks(tmp_path: Path) -> None:
    real_dir = tmp_path / "nix-store-pkg"
    real_dir.mkdir()
    stamp = real_dir / "diagnostic-build.json"
    stamp.write_text(json.dumps({"version": "0.1.22", "revision": "a" * 40}), encoding="utf-8")

    sym_stamp = tmp_path / "linked-stamp.json"
    sym_stamp.symlink_to(stamp)
    raw = dsh_diagnostics._read_bytes_bounded(sym_stamp, 2048)
    assert raw is not None
    assert json.loads(raw.decode("utf-8"))["version"] == "0.1.22"

    # Symlinked dist-info directory inside site-packages is also discovered cleanly
    sp = tmp_path / "site-packages"
    sp.mkdir()
    real_dist = tmp_path / "wheel-cache" / "mindie_sim-0.9.17.dist-info"
    real_dist.mkdir(parents=True)
    (sp / "mindie_sim-0.9.17.dist-info").symlink_to(real_dist, target_is_directory=True)
    found = dsh_diagnostics._scan_subdirs_bounded(sp, prefix="mindie_sim-", suffix=".dist-info")
    assert len(found) == 1


def test_skills_and_references_contain_zero_benchmark_spillover() -> None:
    import re
    import yaml

    banned = (
        "PROGRESS.md",
        "mindie-bench-ablation/1",
        "mindie_knowledge.graded.experiment_ledger",
        "workspace-setup.md",
        "github-authentication.md",
        "user-notice.md",
        "needs_onboarding_prompt",
        "vllm-ascend-workspace",
        "gh auth login",
        "GitHub Star",
        "Non-Intrusive First-Use Onboarding",
        "disable-model-invocation",
        "disableModelInvocation",
    )
    slash_re = re.compile(r"(?<![A-Za-z0-9_./:-}])/mindie-agent(?![/A-Za-z0-9_-])")
    skill_md = ROOT / "skills" / "mindie-agent" / "SKILL.md"
    skill_raw = skill_md.read_text(encoding="utf-8")
    assert len(skill_raw.splitlines()) <= 30
    frontmatter = yaml.safe_load(skill_raw.split("---", 2)[1])
    assert frontmatter["name"] == "mindie-agent"
    assert isinstance(frontmatter["description"], str) and frontmatter["description"]
    skill_files = [skill_md, *sorted((ROOT / "skills" / "mindie-agent" / "references").glob("*.md"))]
    assert len(skill_files) >= 5
    for doc in skill_files:
        text = doc.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{doc.name} still contains banned spillover/install token {token!r}"
        assert not slash_re.search(text), f"{doc.name} still mentions user command /mindie-agent"
    for user_doc in (
        ROOT / "package.json",
        ROOT / "cordis.patch.yml",
        ROOT / "README.md",
        ROOT / "README.en.md",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "README.en.md",
        *sorted((ROOT / "docs" / "install").glob("*.md")),
    ):
        assert not slash_re.search(user_doc.read_text(encoding="utf-8")), f"{user_doc} mentions /mindie-agent"
    for name in ("workspace-setup.md", "github-authentication.md", "user-notice.md"):
        assert (ROOT / "docs" / "install" / name).is_file()
        assert not (ROOT / "skills" / "mindie-agent" / "references" / name).exists()


def test_stop_fast_noop_under_100ms_and_opted_in_auto_lease_alignment(tmp_path: Path) -> None:
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod
    from mindie_knowledge.loop.activation import Admission

    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    state_dir = data_dir / "state"
    proj_dir = tmp_path / "project"
    outside_dir = tmp_path / "outside"
    other_dir = tmp_path / "other"
    for d in (cfg_dir, data_dir, state_dir, proj_dir, outside_dir, other_dir):
        d.mkdir(parents=True, exist_ok=True)
    consent = cfg_dir / "mindie-consent.json"
    comm = cfg_dir / "mindie-community.json"
    adm_path = data_dir / "admission.sqlite3"
    engine = cfg_dir / "engine.json"
    engine.write_text(
        json.dumps(
            {
                "root": str(data_dir / "knowledge"),
                "domain": "vllm-ascend",
                "community_config": str(comm),
                "admission_path": str(adm_path),
                "transcript_adapter": str(SCRIPTS / "dsh_transcript.py"),
                "redactor_executable": sys.executable,
            }
        ),
        encoding="utf-8",
    )
    adapter = cfg_dir / "config.json"
    adapter.write_text(
        json.dumps(
            {
                "python": sys.executable,
                "venv_dir": sys.prefix,
                "engine_config": str(engine),
                "state_dir": str(state_dir),
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "MINDIE_DSH_CONFIG": str(adapter),
        "MINDIE_DSH_CONFIG_HOME": str(cfg_dir),
        "MINDIE_DSH_DATA_HOME": str(data_dir),
        "MINDIE_DSH_ENGINE_CONFIG": str(engine),
        "MINDIE_DSH_STATE_DIR": str(state_dir),
        "MINDIE_DIAGNOSTICS_ROOT": str(tmp_path / "diag"),
    }

    # 1. Sharing disabled: mindie_launch hook stop exits < 100ms (min of 5 warm samples) with zero SQLite
    consent_store.record_choice(consent, "disabled")
    settings_mod.write(comm, enabled=False, repository=None, project_roots=[], consent_config=str(consent))
    subprocess.run([sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"], env=env, capture_output=True, check=True, timeout=10)
    samples_launch = []
    for _ in range(5):
        t0 = time.perf_counter()
        res = subprocess.run([sys.executable, str(SCRIPTS / "mindie_launch.py"), "hook", "stop"], env=env, capture_output=True, check=True, timeout=10)
        samples_launch.append((time.perf_counter() - t0) * 1000.0)
        assert res.stdout.strip() == b"{}"
    assert min(samples_launch) < 100.0, samples_launch
    assert not adm_path.exists()

    # 2. Out-of-scope cwd: capture_stop.py exits < 100ms (min of 5 warm samples) with zero SQLite
    consent_store.record_choice(consent, "contribute")
    settings_mod.write(
        comm,
        enabled=True,
        repository="mindie-agent/knowledge-vllm-ascend",
        project_roots=[proj_dir],
        consent_config=str(consent),
    )
    oos_event = json.dumps({"hook_event_name": "Stop", "session_id": "sess-oos", "cwd": str(outside_dir)}).encode()
    subprocess.run([sys.executable, str(SCRIPTS / "capture_stop.py")], input=oos_event, env=env, capture_output=True, check=True, timeout=10)
    samples_oos = []
    for _ in range(5):
        t0 = time.perf_counter()
        subprocess.run([sys.executable, str(SCRIPTS / "capture_stop.py")], input=oos_event, env=env, capture_output=True, check=True, timeout=10)
        samples_oos.append((time.perf_counter() - t0) * 1000.0)
    assert min(samples_oos) < 100.0, samples_oos
    assert not adm_path.exists()

    # 3. Opted-in in-scope Stop aligns an earlier lease's project_root and initializes state-v4.sqlite3
    adm = Admission(adm_path)
    adm.activate("sess-in-scope", project_root=str(other_dir), not_before=0.0)
    transcript = tmp_path / "session.v4.jsonl.zstd"
    transcript.write_bytes(b"")
    in_event = json.dumps(
        {
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "session_id": "sess-in-scope",
            "identity_kind": "turn",
            "turn_id": "1",
            "transcript_path": str(transcript),
            "harness": "dsh",
            "cwd": str(proj_dir),
        }
    ).encode()
    completed = subprocess.run([sys.executable, str(SCRIPTS / "capture_stop.py")], input=in_event, env=env, capture_output=True, check=True, timeout=15)
    assert completed.returncode == 0
    from mindie_knowledge import state_layout

    aligned = adm.knowledge_lease("sess-in-scope")
    assert aligned is not None
    assert Path(aligned["project_root"]).resolve() == proj_dir.resolve()
    assert (state_layout.state_root(data_dir / "knowledge", "vllm-ascend") / state_layout.DATABASE).is_file()


def test_v0_1_22_config_and_pointer_upgrade_compatibility(tmp_path: Path, monkeypatch) -> None:
    import mindie_paths

    cfg_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    gen_dir = data_dir / "update" / "generations" / "v0.1.22" / "package"
    gen_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "host3.mjs", gen_dir / "host3.mjs")
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "mindie-consent.json").write_text(
        json.dumps({"schema": "mindie-consent/1", "choice": "disabled", "choice_revision": 2}),
        encoding="utf-8",
    )
    (cfg_dir / "config.json").write_text(
        json.dumps(
            {
                "version": "0.1.22",
                "package_dir": str(gen_dir),
                "python": sys.executable,
                "venv_dir": str(Path(sys.executable).parent.parent),
                "engine_config": str(cfg_dir / "engine.json"),
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "update" / "current.json").write_text(
        json.dumps({"generation": str(gen_dir), "version": "0.1.22", "previous_generation": None}),
        encoding="utf-8",
    )
    monkeypatch.setenv("MINDIE_DSH_CONFIG_HOME", str(cfg_dir))
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(data_dir))
    monkeypatch.delenv("MINDIE_DSH_CONFIG", raising=False)

    assert mindie_paths.venv_python() == Path(sys.executable)
    node = mindie_paths.which_native("node")
    if node:
        proc = subprocess.run(
            [node, "-e", f"const m = require({json.dumps(str(ROOT / 'paths.cjs'))}); console.log(JSON.stringify({{...m.paths(), ...m.seedPaths()}}));"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        js_paths = json.loads(proc.stdout)
        assert Path(js_paths["venvPython"]) == Path(sys.executable)
        assert Path(js_paths["seedArchive"]).name == "mindie-harness-seed.pyz"
