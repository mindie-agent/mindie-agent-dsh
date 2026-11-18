"""End-to-end pre-0.1.26 (`v0.1.25 -> v0.1.27`) DSH self-upgrade handoff tests."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
NO_WIN = 0x08000000 if os.name == "nt" else 0


def test_v0125_setup_candidate_two_phase_handoff_and_legacy_migration(tmp_path: Path, monkeypatch) -> None:
    cfg_base = tmp_path / "cfg-base"
    workspace = tmp_path / "authorized-ws"
    workspace.mkdir()
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(cfg_base))
    for key in ("MINDIE_DSH_CONFIG", "MINDIE_AGENT_CONFIG", "MINDIE_DSH_CONFIG_HOME"):
        monkeypatch.delenv(key, raising=False)
    legacy_dir = cfg_base / "dsh"
    legacy_dir.mkdir(parents=True)
    legacy_cfg = legacy_dir / "config.json"
    legacy_engine = legacy_dir / "engine.json"
    legacy_consent = legacy_dir / "mindie-consent.json"
    legacy_comm = legacy_dir / "mindie-community.json"
    custom_root = tmp_path / "custom-dsh-knowledge"
    legacy_engine.write_text(json.dumps({"root": str(custom_root), "domain": "operators"}), encoding="utf-8")
    legacy_consent.write_text(json.dumps({
        "schema": "mindie-consent/1", "choice": "local-only", "enabled": True, "project_roots": [str(workspace)],
    }), encoding="utf-8")
    legacy_comm.write_text(json.dumps({
        "schema": "mindie-community-config/1", "enabled": True, "project_roots": [str(workspace)],
        "consent_config": str(legacy_consent),
    }), encoding="utf-8")
    legacy_cfg.write_text(json.dumps({
        "python": "/nonexistent/v0.1.25/python",
        "engine_config": str(legacy_engine),
        "domain": "operators",
    }), encoding="utf-8")

    env = dict(os.environ, MINDIE_REDACTOR_EXECUTABLE=sys.executable)
    canonical_cfg = cfg_base / "dsh.json"

    # Phase 0: --prepare-only must reject an unsatisfied component floor BEFORE native_install
    import mindie_knowledge.component_bindings as cb
    import pytest

    spec = importlib.util.spec_from_file_location("dsh_setup_prep_mod", SCRIPTS / "setup.py")
    assert spec is not None and spec.loader is not None
    prep_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep_mod)
    too_high_cfg = dict(
        json.loads(legacy_cfg.read_text(encoding="utf-8")),
        component_commands={"sim": [sys.executable, "-m", "mindie_sim"]},
    )
    legacy_cfg.write_text(json.dumps(too_high_cfg), encoding="utf-8")
    monkeypatch.setattr(cb, "required_components", lambda *a, **kw: {"mindie-sim": "999.0.0"})
    with pytest.raises(ValueError, match="mindie-sim"):
        prep_mod._init(prep_mod._profile(), ["--prepare-only"])

    # Restore valid legacy_cfg and run Phase 1 (--prepare-only)
    legacy_cfg.write_text(json.dumps({
        "python": "/nonexistent/v0.1.25/python",
        "engine_config": str(legacy_engine),
        "domain": "operators",
    }), encoding="utf-8")
    prep = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "setup.py"),
         "--python", "/nonexistent/v0.1.25/python", "--total-timeout", "600", "--prepare-only"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, creationflags=NO_WIN,
    )
    assert prep.returncode == 0, prep.stderr
    prep_doc = json.loads(prep.stdout)
    assert prep_doc["status"] == "prepared"
    assert prep_doc["version"] == "0.1.27"
    assert not canonical_cfg.exists()
    assert not (cfg_base / "mindie-community.json").exists()

    # Phase 2: apply=True migrates config, engine, consent, and community settings
    apply_proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPTS / "setup.py"),
         "--python", "/nonexistent/v0.1.25/python", "--total-timeout", "600"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, creationflags=NO_WIN,
    )
    assert apply_proc.returncode == 0, apply_proc.stderr
    applied = json.loads(apply_proc.stdout)
    assert applied["status"] == "ready"
    assert applied["version"] == "0.1.27"
    assert applied["python"] == sys.executable
    saved = json.loads(canonical_cfg.read_text(encoding="utf-8"))
    assert saved["python"] == sys.executable
    assert saved["domain"] == "operators"
    assert json.loads(legacy_cfg.read_text(encoding="utf-8"))["python"] == sys.executable
    eng = json.loads((cfg_base / "dsh.engine.json").read_text(encoding="utf-8"))
    assert eng["root"] == str(custom_root)
    assert eng["domain"] == "operators"
    migrated_comm = json.loads((cfg_base / "mindie-community.json").read_text(encoding="utf-8"))
    assert migrated_comm["enabled"] is True and migrated_comm["project_roots"] == [str(workspace)]


def test_same_interp_distinguishes_posix_venv_symlinks(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("dsh_setup_mod", SCRIPTS / "setup.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    venv_a = tmp_path / "venv-a" / "bin"
    venv_b = tmp_path / "venv-b" / "bin"
    venv_a.mkdir(parents=True)
    venv_b.mkdir(parents=True)
    target = tmp_path / "real-python"
    target.write_text("", encoding="utf-8")
    p_a, p_b = venv_a / "python", venv_b / "python"
    if os.name == "nt":
        p_a.write_text("", encoding="utf-8")
        p_b.write_text("", encoding="utf-8")
    else:
        p_a.symlink_to(target)
        p_b.symlink_to(target)
    assert not mod._same_interp(p_a, p_b)
    assert mod._same_interp(p_a, p_a)


def test_package_json_files_include_legacy_updater_artifacts() -> None:
    pkg = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    files = set(pkg["files"])
    for required in ("pyproject.toml", "uv.lock", "runtime-requirements.txt", "harness-compatibility.json", "assets/", "scripts/", "skills/"):
        assert required in files, required
