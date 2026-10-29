"""Bound components survive a core-only updater's first candidate setup."""
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import component_update
import mindie_paths
from mindie_knowledge import component_runtime
from test_setup_budget import setup


def test_isolated_candidate_with_sufficient_bound_components_needs_no_build(tmp_path):
    config = mindie_paths.config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    value = {"component_commands": {
        "profiler": [sys.executable, "-m", "mindie_profiler"],
        "sim": ["/user/custom-simulator", "--mode"],
    }}
    config.write_text(json.dumps(value))
    result = subprocess.run(
        [sys.executable, "-I", str(Path(component_update.__file__)),
         str(mindie_paths.PACKAGE_DIR), sys.executable, "--ensure-bound", "--timeout", "5"],
        cwd=tmp_path, capture_output=True, text=True, timeout=7,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) is None
    assert json.loads(config.read_text()) == value
    assert not (mindie_paths.data_home() / "update/component-runtimes").exists()


def test_missing_bound_component_uses_existing_runtime_builder(tmp_path, monkeypatch):
    config = mindie_paths.config_file()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"component_commands": {
        "profiler": [sys.executable, "-m", "mindie_profiler"]}}))
    installed = component_runtime.read_inventory(sys.executable)
    profiler_version = metadata.version("mindie-profiler")
    version = metadata.version
    def missing(name):
        if name == "mindie-profiler":
            raise metadata.PackageNotFoundError(name)
        return version(name)
    monkeypatch.setattr(component_update.metadata, "version", missing)
    discover = Mock(return_value={"installed": {}, "runtime": installed, "changed": True})
    build = Mock(return_value={
        "generation": str(tmp_path), "source": str(tmp_path / "source"),
        "python": str(tmp_path / "runtime/.venv/bin/python"), "components": {"runtime": installed},
    })
    monkeypatch.setattr(component_runtime, "discover", discover)
    monkeypatch.setattr(component_runtime, "build", build)
    artifact = component_update.prepare(mindie_paths.PACKAGE_DIR, sys.executable,
                                        ensure_bound=True, timeout=400)
    assert artifact == build.return_value
    assert discover.call_args.kwargs["required_components"] == {"mindie-profiler": profiler_version}
    assert 0 < build.call_args.kwargs["timeout"] <= 395
    assert build.call_args.args[3]["mindie-profiler"] == installed["mindie-profiler"]


def test_old_parent_budget_selects_completed_runtime_without_nested_setup(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"component_commands": {"profiler": [sys.executable, "-m", "mindie_profiler"]}}))
    selected = tmp_path / "selected/runtime/.venv"
    executable = selected / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    monkeypatch.setattr(setup.mp, "config_file", lambda: config)
    monkeypatch.setattr(setup, "RUN_DEADLINE", setup.time.monotonic() + 600)
    monkeypatch.setattr(setup, "INSTALL_RUNTIME_DIR", Path(sys.prefix))
    calls = []
    def prepared(argv, *, timeout):
        calls.append((argv, timeout))
        return subprocess.CompletedProcess(argv, 0, json.dumps({"python": str(executable)}), "")
    monkeypatch.setattr(setup, "run", prepared)
    assert setup.prepare_bound_runtime()
    assert setup.runtime_python() == executable
    argv, outer = calls[0]
    assert len(calls) == 1
    assert Path(argv[2]).name == "component_update.py"
    assert float(argv[-1]) + 5 <= outer <= 415
    assert setup.RUN_DEADLINE - setup.time.monotonic() - outer >= 180


def test_late_setup_failure_keeps_successful_activation_visible(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "runtime_python", lambda: tmp_path / "unavailable-python")
    publication = {"status": "active", "domains": {"status": "retired"}}
    setup.report_setup_failure(setup.SetupError("reporter activation timed out"),
                               activation=publication)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "partial"
    assert result["activation"] == publication
    assert result["error"] == "reporter activation timed out"
