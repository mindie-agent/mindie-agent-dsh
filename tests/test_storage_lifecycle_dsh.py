"""Cross-platform storage lifecycle regressions for the DSH adapter.

[Role]
Exercises the four historical DSH layouts, rollback-target/live-lease
protection, the single merged component runtime, archive cleanup, bounded
pre-build uv cache placement, and the test-environment temp isolation.

[Invariants]
- Every fixture path stays under pytest `tmp_path`; no real user cache is read.
- The current and previous generation, the profile symlink target, and a live
  child lease survive a forced sweep; after the lease is released the same
  generation is reclaimable.
- `updater.recover()` still finds the previous generation after the sweep.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tarfile
import threading
import time
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import setup  # noqa: E402
import storage_lifecycle  # noqa: E402
import updater  # noqa: E402



def _age(path: Path, seconds: float = 3 * 86400) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_layout(tmp_path: Path, monkeypatch) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    state = tmp_path / "state" / "mindie-agent"
    data = state / "dsh"
    config = state / "config" / "dsh"
    for directory in (data, config):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(data))
    monkeypatch.setenv("MINDIE_DSH_CONFIG_HOME", str(config))
    monkeypatch.setenv("MINDIE_DSH_STATE_DIR", str(data / "state"))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(state))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(state / "config"))

    # Legacy architecture: dsh/venv.
    _write(data / "venv" / "old" / "sentinel")
    _age(data / "venv")

    # Six runtimes: active binding, live-lease candidate, and excess keys.
    runtimes = {}
    for index, name in enumerate(("live", "k2", "k3", "k4", "k5", "active")):
        directory = data / "runtimes" / name
        _write(directory / "venv" / "bin" / "python")
        runtimes[name] = directory
        _age(directory, 3 * 86400 - index * 60)
    active_python = runtimes["active"] / "venv" / "bin" / "python"

    engines = config / "engines"
    engines.mkdir()
    active_engine = engines / "active.json"
    for name in ("active", "live", "k2", "k3", "k4", "k5"):
        _write(engines / f"{name}.json", "{}")
        _age(engines / f"{name}.json")

    # Four generations with historical tarballs. v0.1.1 is only pinned by a
    # profile link, v0.1.2 by previous_generation, v0.1.3 by generation.
    generations = {}
    for index in range(4):
        name = f"v0.1.{index}"
        generation = data / "update" / "generations" / name
        _write(generation / "package" / "package.json", json.dumps({"version": f"0.1.{index}"}))
        _write(generation / "package" / "asset.tgz", "payload")
        _write(generation / "asset.tgz", "tarball")
        generations[name] = generation
        _age(generation)

    # A profile symlink pin to v0.1.1 (not current/previous).
    profile_link = home / ".dsh" / "profiles" / "web" / "node_modules" / "@mindie-agent" / "dsh"
    profile_link.parent.mkdir(parents=True)
    profile_linked = False
    try:
        profile_link.symlink_to(generations["v0.1.1"], target_is_directory=True)
        profile_linked = True
    except OSError:
        # Windows can deny unprivileged symlink creation; the direct
        # `dsh_pins` assertion below still covers the pin contract.
        pass

    # Four component runtimes; the binding points into the active one.
    components = data / "update" / "component-runtimes"
    for index in range(4):
        directory = components / f"c{index}"
        _write(directory / "runtime" / ".venv" / "bin" / "python")
        _write(directory / "components.json", "{}")
        _age(directory)
    active_component_python = components / "c3" / "runtime" / ".venv" / "bin" / "python"

    current = {
        "schema": "mindie-dsh-generation/1",
        "generation": str(generations["v0.1.3"]),
        "previous_generation": str(generations["v0.1.2"]),
        "tag": "v0.1.3",
        "previous_tag": "v0.1.2",
    }
    _write(data / "update" / "current.json", json.dumps(current))
    binding = {
        "schema": "mindie-dsh-adapter/1",
        "version": "0.1.22",
        "python": str(active_component_python),
        "venv_dir": str(runtimes["active"] / "venv"),
        "package_dir": str(generations["v0.1.3"] / "package"),
        "engine_config": str(active_engine),
    }
    _write(config / "config.json", json.dumps(binding))

    # Orphaned dedicated uv cache (old enough to be swept).
    uv_cache = home / ".cache" / "mindie-agent-dsh" / "uv-cache"
    _write(uv_cache / "stale")
    _age(uv_cache)

    return {
        "home": home,
        "data": data,
        "config": config,
        "runtimes": runtimes,
        "engines": engines,
        "generations": generations,
        "components": components,
        "uv_cache": uv_cache,
        "profile_link": profile_link,
        "profile_linked": profile_linked,
        "active_python": active_python,
    }


def _lease_child(directory: Path):
    code = (
        "import sys\n"
        "from mindie_knowledge.storage import acquire_runtime_lease\n"
        "lease = acquire_runtime_lease(sys.argv[1])\n"
        "print('ready', flush=True)\n"
        "sys.stdin.readline()\n"
        "lease.release()\n"
    )
    child = subprocess.Popen(
        [sys.executable, "-I", "-c", code, str(directory)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", env=dict(os.environ),
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )
    output: queue.Queue[str] = queue.Queue()
    reader = threading.Thread(target=lambda: output.put(child.stdout.readline()), daemon=True)
    reader.start()
    try:
        ready = output.get(timeout=15)
    except queue.Empty:
        child.kill()
        child.wait(timeout=5)
        raise AssertionError("lease child did not become ready: " + child.stderr.read())
    if ready.strip() != "ready":
        child.kill()
        child.wait(timeout=5)
        raise AssertionError("lease child failed: " + child.stderr.read())
    return child


def _stop_child(child) -> None:
    if child.poll() is None:
        try:
            child.stdin.write("\n")
            child.stdin.flush()
            child.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            child.kill()
            child.wait(timeout=5)


def test_four_legacy_layouts_keep_current_previous_profile_and_live_lease(tmp_path, monkeypatch):
    layout = _make_layout(tmp_path, monkeypatch)
    from mindie_knowledge.storage import leases as storage_leases
    from mindie_knowledge.storage import legacy as storage_legacy
    fake_live = lambda timeout=None: (set(), True)
    monkeypatch.setattr(storage_leases, "live_process_prefixes", fake_live)
    monkeypatch.setattr(storage_legacy, "live_process_prefixes", fake_live)
    monkeypatch.setattr("mindie_knowledge.storage.live_process_prefixes", fake_live)
    data, config = layout["data"], layout["config"]
    live = layout["runtimes"]["live"]
    child = _lease_child(live)
    _age(live)
    try:
        first = storage_lifecycle.sweep(stage="test-pre-build", force=True)
        assert first["status"] in {"completed", "partial"}
        assert layout["generations"]["v0.1.3"].is_dir()
        assert layout["generations"]["v0.1.2"].is_dir()
        assert (layout["generations"]["v0.1.3"] / "package" / "package.json").is_file()
        assert (layout["generations"]["v0.1.2"] / "package" / "package.json").is_file()
        assert not (layout["generations"]["v0.1.0"]).exists()
        if layout["profile_linked"]:
            assert layout["generations"]["v0.1.1"].is_dir()
        assert not (data / "venv").exists()
        assert not layout["uv_cache"].exists()
        assert layout["runtimes"]["active"].is_dir()
        assert live.is_dir(), "a live shared runtime lease must survive the sweep"
        assert (layout["components"] / "c3").is_dir(), "the active component binding survives"
        assert not (layout["components"] / "c0").exists()

        # Current/previous generations keep their package payload, while any
        # historical tarball was reclaimed from the retained generations.
        for name in ("v0.1.2", "v0.1.3"):
            assert not (layout["generations"][name] / "asset.tgz").exists()
        assert not (layout["generations"]["v0.1.0"]).exists()
        assert all(not path.exists() for path in layout["engines"].glob("k[2-4].json"))

        # The direct pin list must name the profile target even when the OS
        # refused symlink creation in this test.
        monkeypatch.setattr(
            storage_lifecycle, "_profile_targets",
            lambda: [layout["generations"]["v0.1.1"]],
        )
        pins = {str(path) for path in storage_lifecycle.dsh_pins()}
        assert str(layout["generations"]["v0.1.1"]) in pins
        assert str(layout["generations"]["v0.1.2"]) in pins
        assert str(layout["generations"]["v0.1.3"]) in pins
    finally:
        _stop_child(child)

    # Once the lease is gone, the same old generation is reclaimable; the
    # current/previous/profile pins still win.
    released = storage_lifecycle.sweep(stage="test-post-release", force=True)
    assert released["status"] in {"completed", "partial"}
    assert not live.exists(), "the released generation must be reclaimable on the next sweep"
    assert layout["generations"]["v0.1.3"].is_dir()
    assert layout["generations"]["v0.1.2"].is_dir()

    # Recovery still resolves the sole previous generation after the sweep.
    monkeypatch.setattr("native_update.install", lambda *args, **kwargs: None)
    monkeypatch.setattr(updater, "setup_candidate", lambda *args, **kwargs: None)
    recovered = updater.recover("web")
    package = Path(recovered["generation"])
    assert recovered["recovered"] is True
    assert package.is_dir()
    assert (package / "package" / "package.json").is_file()


def test_release_extraction_discards_asset_and_component_setup_reuses_interpreter(tmp_path, monkeypatch):
    source = tmp_path / "release"
    package = source / "package"
    _write(package / "package.json", json.dumps({"version": "0.1.22"}))
    _write(package / "pyproject.toml", "[project]\nname='x'\n")
    archive = source / "asset.tgz"
    with tarfile.open(archive, "w:gz") as stream:
        stream.add(package, arcname="package")
    digest = __import__("hashlib").sha256(archive.read_bytes()).hexdigest()
    sums = source / "SHA256SUMS"
    sums.write_text(digest + "  mindie-agent-dsh-0.1.22.tgz\n", encoding="utf-8")

    generation = tmp_path / "data" / "update" / "generations" / "v0.1.22"
    staged = generation / "package"
    merged_python = str(tmp_path / "update" / "component-runtimes" / "c1" / "runtime/.venv/bin/python")
    artifact = {
        "source": str(staged),
        "generation": str(generation),
        "python": merged_python,
        "components": {"mindie-knowledge": "0.13.22"},
    }
    calls = []
    original_setup_candidate = updater.setup_candidate

    def fake_download(url, destination, limit=128 * 1024 * 1024):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((archive if url.endswith("/asset") else sums).read_bytes())

    def fake_setup_candidate(package_path, python, *, artifact=None, apply=True):
        calls.append({"package": package_path, "python": python, "artifact": artifact, "apply": apply})

    release = {
        "tag_name": "v0.1.22", "draft": False, "prerelease": False,
        "assets": [
            {"name": "mindie-agent-dsh-0.1.22.tgz",
             "browser_download_url": "https://example.invalid/v0.1.22/asset"},
            {"name": "SHA256SUMS",
             "browser_download_url": "https://example.invalid/v0.1.22/SHA256SUMS"},
        ],
    }
    monkeypatch.setattr(updater, "fetch_releases", lambda _api: [release])
    monkeypatch.setattr(updater, "download", fake_download)
    monkeypatch.setattr(updater, "setup_candidate", fake_setup_candidate)
    monkeypatch.setattr(updater.storage_lifecycle, "sweep",
                        lambda **kwargs: {"status": "completed", "stage": kwargs.get("stage")})
    monkeypatch.setattr("native_update.current_version", lambda _profile: None)
    monkeypatch.setattr("native_update.install", lambda *args, **kwargs: None)
    monkeypatch.setattr("component_update.prepare", lambda *args, **kwargs: artifact)

    result = updater.check("web", "0.1.21", "https://example.invalid")
    assert result["state"] == "applied"
    assert not (generation / "asset.tgz").exists()
    assert not (generation / "SHA256SUMS").exists()
    assert len(calls) == 2
    assert all(call["artifact"] is artifact for call in calls)
    assert all(call["artifact"]["python"] == merged_python for call in calls)
    assert all(call["python"] == merged_python for call in calls)

    with mock.patch.object(updater, "run", return_value=(0, "")) as executed:
        original_setup_candidate(
            staged, "/must-not-build-a-second-venv",
            artifact={"python": merged_python, "generation": str(generation)},
        )
    command = executed.call_args.args[0]
    assert command[0] == merged_python
    assert "--component-manifest" in command


def test_component_manifest_never_calls_a_second_uv_sync(tmp_path, monkeypatch):
    manifest = tmp_path / "update" / "component-runtimes" / "c1" / "components.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"runtime": {}}), encoding="utf-8")
    runtime = manifest.parent / "runtime" / ".venv"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin" / "python").write_text("x", encoding="utf-8")
    config_file = tmp_path / "config" / "config.json"
    config_file.parent.mkdir(exist_ok=True)
    versions = {name: "test" for name in setup.COMPONENTS}

    monkeypatch.setattr(setup, "check_host", lambda: None)
    monkeypatch.setattr(setup, "probe_runtime", lambda **kwargs: {"versions": versions})
    monkeypatch.setattr(setup, "adapter_configuration", lambda: {
        "python": str(runtime / "bin" / "python"),
        "venv_dir": str(runtime),
        "engine_config": str(tmp_path / "config" / "engine.json"),
        "domain": "vllm-ascend",
    })
    monkeypatch.setattr(setup, "install_redactor", lambda **kwargs: str(tmp_path / "redactor"))
    monkeypatch.setattr(setup, "engine_configuration", lambda _redactor: {
        "domain": "vllm-ascend", "feeds": [],
    })
    monkeypatch.setattr(setup, "prepare_domains", lambda _adapter, _engine: {})
    monkeypatch.setattr(setup, "publish_binding", lambda *args, **kwargs: {"status": "active"})
    monkeypatch.setattr(setup, "install_updater", lambda: {})
    monkeypatch.setattr(setup, "sync_runtime", lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("a second uv sync must not run for a component manifest")))
    monkeypatch.setattr(setup.storage_lifecycle, "sweep",
                        lambda **kwargs: {"status": "completed", "stage": kwargs.get("stage")})
    monkeypatch.setattr(setup.mp, "config_file", lambda: config_file)
    monkeypatch.setattr(setup.mp, "data_home", lambda: tmp_path / "data")
    monkeypatch.setattr(setup.mp, "config_home", lambda: tmp_path / "config")
    monkeypatch.setattr(setup.mp, "state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(setup.mp, "same_path", lambda *_args: True)
    monkeypatch.setattr(setup, "run", lambda *args, **kwargs: subprocess.CompletedProcess(
        args=args, returncode=0, stdout='{"status": "disabled"}\n', stderr=""))
    monkeypatch.setattr("mindie_knowledge.component_runtime.source_baseline", lambda *_args: {})
    monkeypatch.setattr("mindie_knowledge.component_releases.runtime_identity", lambda *_args: "identity")
    monkeypatch.setattr("mindie_knowledge.runtime_inventory.verify", lambda *_args: None)
    monkeypatch.setattr("mindie_knowledge.runtime_inventory.verify_lock", lambda *_args: None)
    try:
        assert setup.main(["--component-manifest", str(manifest)]) == 0
        assert setup.INSTALL_RUNTIME_DIR == runtime
    finally:
        setup.INSTALL_RUNTIME_DIR = None
        setup.RUN_DEADLINE = None


def test_prebuild_uv_cache_is_same_volume_only_when_needed(tmp_path, monkeypatch):
    data_home = tmp_path / "data"
    data_home.mkdir(exist_ok=True)
    env = {"HOME": str(tmp_path / "home")}
    monkeypatch.setattr(storage_lifecycle, "_device", lambda path: 2 if "uv-cache" in str(path) else 1)
    configured = storage_lifecycle.configure_prebuild_uv_cache(env, data_home=data_home)
    assert configured["UV_CACHE_DIR"] == str(data_home / ".uv-cache")

    explicit = {"UV_CACHE_DIR": str(tmp_path / "explicit")}
    assert storage_lifecycle.configure_prebuild_uv_cache(
        explicit, data_home=data_home)["UV_CACHE_DIR"] == str(tmp_path / "explicit")

    same = {"HOME": str(tmp_path / "home")}
    monkeypatch.setattr(storage_lifecycle, "_device", lambda _path: 2)
    assert "UV_CACHE_DIR" not in storage_lifecycle.configure_prebuild_uv_cache(same, data_home=data_home)

    captured = {}

    def fake_run(argv, *, timeout, env=None, cwd=None):
        captured.update(env or {})
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(setup, "runtime_dir", lambda: data_home / "runtimes" / "k" / "venv")
    monkeypatch.setattr(setup, "runtime_python", lambda: Path(sys.executable))
    monkeypatch.setattr(setup.mp, "data_home", lambda: data_home)
    monkeypatch.setattr(setup, "run", fake_run)
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    monkeypatch.setattr(storage_lifecycle, "_device", lambda path: 2 if "uv-cache" in str(path) else 1)
    setup.sync_runtime("3.11", timeout=30)
    assert captured["UV_CACHE_DIR"] == str(data_home / ".uv-cache")


def test_isolated_test_environment_owns_temp_and_uv_cache():
    tmp = Path(os.environ["TMPDIR"]).resolve()
    assert str(tmp).startswith(str(Path(os.environ["MINDIE_DSH_DATA_HOME"]).resolve().parent))
    assert Path(os.environ["UV_CACHE_DIR"]).resolve().is_dir()
    assert Path(os.environ["UV_CACHE_DIR"]).resolve().name == "uv-cache"


def test_safe_sweep_degrades_a_failed_secondary_cleanup(monkeypatch):
    def explode(**_kwargs):
        raise RuntimeError("synthetic gc failure")

    monkeypatch.setattr(storage_lifecycle, "sweep", explode)
    summary = storage_lifecycle.safe_sweep(stage="test-secondary")
    assert summary["stage"] == "test-secondary"
    assert summary["status"] in {"failed", "partial"}
    assert summary["partial"] is True


def test_migrated_data_home_and_custom_profile_keep_rollback_generations(tmp_path, monkeypatch):
    home = tmp_path / "home"
    default_shared = home / ".local" / "share" / "mindie-agent"
    default_dsh = default_shared / "dsh"
    custom_data = tmp_path / "custom" / "dsh"
    custom_config = tmp_path / "custom" / "config" / "dsh"
    custom_home = tmp_path / "custom-home"
    for directory in (default_dsh, custom_data, custom_config):
        directory.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(custom_data))
    monkeypatch.setenv("MINDIE_DSH_CONFIG_HOME", str(custom_config))
    monkeypatch.setenv("MINDIE_DSH_STATE_DIR", str(custom_data / "state"))
    monkeypatch.delenv("MINDIE_DATA_HOME", raising=False)
    monkeypatch.setenv("DSH_HOME", str(custom_home))

    generations = {}
    for name, age_days in (("v0.1.0", 9), ("v0.1.1", 8), ("v0.1.9", 7),
                           ("v0.1.2", 3), ("v0.1.3", 1)):
        generation = default_dsh / "update" / "generations" / name
        _write(generation / "package" / "package.json", json.dumps({"version": name[1:]}))
        _age(generation, age_days * 86400)
        generations[name] = generation
    custom_generation = custom_data / "update" / "generations" / "v0.1.4"
    _write(custom_generation / "package" / "package.json", json.dumps({"version": "0.1.4"}))
    _age(custom_generation, 2 * 86400)
    _write(custom_data / "update" / "current.json", json.dumps({
        "schema": "mindie-dsh-generation/1",
        "generation": str(custom_generation / "package"),
        "previous_generation": str(generations["v0.1.1"] / "package"),
        "tag": "v0.1.4",
        "previous_tag": "v0.1.1",
    }))
    profile = custom_home / "profiles" / "web" / "node_modules" / "@mindie-agent"
    profile.mkdir(parents=True)
    try:
        (profile / "dsh").symlink_to(generations["v0.1.0"], target_is_directory=True)
    except OSError:
        pytest.skip("this filesystem cannot create a profile symlink")

    summary = storage_lifecycle.safe_sweep(stage="test-migrated", force=True)
    assert summary["status"] in {"completed", "partial"}
    assert generations["v0.1.0"].is_dir(), "the custom DSH_HOME profile target survives"
    assert generations["v0.1.1"].is_dir(), "previous_generation in the default root survives"
    assert (generations["v0.1.1"] / "package" / "package.json").is_file()
    assert not generations["v0.1.9"].exists(), "the unreferenced historical generation is reclaimed"
    assert custom_generation.is_dir()

    monkeypatch.setattr("native_update.install", lambda *args, **kwargs: None)
    monkeypatch.setattr(updater, "setup_candidate", lambda *args, **kwargs: None)
    recovered = updater.recover("web")
    assert recovered["recovered"] is True
    assert Path(recovered["generation"]) == generations["v0.1.1"] / "package"
    assert Path(recovered["generation"]).is_dir()


def test_orphan_engine_prune_keeps_cross_plugin_reference_and_defers_incomplete_reads(
        tmp_path, monkeypatch):
    home = tmp_path / "home"
    data, config, shared = tmp_path / "data", tmp_path / "config", tmp_path / "shared"
    for directory in (data, config, shared / "config"):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(data))
    monkeypatch.setenv("MINDIE_DSH_CONFIG_HOME", str(config))
    monkeypatch.setenv("MINDIE_DSH_STATE_DIR", str(data / "state"))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(shared))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(shared / "config"))

    _write(data / "runtimes" / "active" / "venv" / "bin" / "python")
    engines = config / "engines"
    for name in ("active", "ghost", "orphan"):
        _write(engines / f"{name}.json", "{}")
        _age(engines / f"{name}.json")
    borrowed = shared / "config" / "cc.json"
    borrowed.write_text(json.dumps({"engine_config": str(engines / "ghost.json")}),
                        encoding="utf-8")

    result = storage_lifecycle._prune_orphan_engines(
        keep_paths=[], deadline=time.monotonic() + 30)
    assert result["status"] == "completed", result
    assert (engines / "ghost.json").is_file(), "a sibling plugin's engine must survive"
    assert (engines / "active.json").is_file(), "the live runtime keeps its engine"
    assert not (engines / "orphan.json").exists(), "an unreferenced orphan is removed"

    _write(engines / "orphan.json", "{}")
    _age(engines / "orphan.json")
    borrowed.write_text("{not-json", encoding="utf-8")
    before = sorted(path.name for path in engines.glob("*.json"))
    deferred = storage_lifecycle._prune_orphan_engines(
        keep_paths=[], deadline=time.monotonic() + 30)
    assert deferred["status"] == "partial"
    assert deferred["partial"] is True
    assert "engine references incomplete" in deferred["warnings"]
    assert sorted(path.name for path in engines.glob("*.json")) == before

    def expire(_path):
        raise TimeoutError("synthetic config deadline")

    monkeypatch.setattr(storage_lifecycle, "_read_document", expire)
    with pytest.raises(TimeoutError):
        storage_lifecycle._prune_orphan_engines(
            keep_paths=[], deadline=time.monotonic() + 30)


def test_sweep_skips_legacy_when_lease_or_profile_or_configs_scan_fails(tmp_path, monkeypatch):
    home, data, config = tmp_path / "home", tmp_path / "data", tmp_path / "config"
    for directory in (home, data, config / "configs", config / "engines"):
        directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("DSH_HOME", str(home / ".dsh"))
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(data))
    monkeypatch.setenv("MINDIE_DSH_CONFIG_HOME", str(config))
    monkeypatch.setenv("MINDIE_DATA_HOME", str(data.parent))
    monkeypatch.setenv("MINDIE_CONFIG_HOME", str(config.parent))

    # 1) Custom configs/other-plugin.json (via symlink) protects an older engine across full sweep.
    for idx, name in enumerate(("newest", "second", "borrowed_old")):
        eng = config / "engines" / f"{name}.json"
        _write(eng, "{}")
        stamp = time.time() - (7200 + idx * 3600)
        os.utime(eng, (stamp, stamp))
    real_cfg = tmp_path / "external-other.json"
    real_cfg.write_text(json.dumps({"engine": str(config / "engines" / "borrowed_old.json")}), encoding="utf-8")
    (config / "configs" / "other-plugin.json").symlink_to(real_cfg)

    ok_result = storage_lifecycle.sweep(stage="test", force=True)
    assert ok_result["status"] == "completed", ok_result
    assert (config / "engines" / "borrowed_old.json").is_file()

    # 2) Lease acquisition failure skips sweep_legacy_installations completely.
    rollback_gen = data / "update" / "generations" / "gen-rollback"
    _write(rollback_gen / "package" / "package.json", "{}")
    swept = []
    monkeypatch.setattr("mindie_knowledge.storage.acquire_runtime_lease",
                        lambda _p: (_ for _ in ()).throw(OSError("lease denied")))
    monkeypatch.setattr("mindie_knowledge.storage.sweep_legacy_installations",
                        lambda **kw: swept.append(kw) or {"status": "completed"})
    lease_fail = storage_lifecycle.sweep(stage="test", force=True, keep_paths=[rollback_gen])
    assert lease_fail["partial"] is True
    assert swept == []

    # 3) Profile directory enumeration PermissionError marks rollback_complete=False and skips sweeps.
    profiles_dir = home / ".dsh" / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    real_iterdir = Path.iterdir
    monkeypatch.setattr(
        Path, "iterdir",
        lambda self: (_ for _ in ()).throw(PermissionError("denied")) if self == profiles_dir else real_iterdir(self),
    )
    prof_fail = storage_lifecycle.sweep(stage="test", force=True)
    assert prof_fail["partial"] is True
    assert swept == []
