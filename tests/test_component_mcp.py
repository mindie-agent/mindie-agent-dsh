"""Exercise default discovery and real component CLI results through the MCP adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import component_mcp
import component_tools


@pytest.fixture
def installed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "adapter.json"
    config.write_text(json.dumps({"python": sys.executable, "version": "test",
                                 "engine_config": "/must-not-read/knowledge.json"}))
    monkeypatch.setenv("MINDIE_DSH_CONFIG", str(config))
    return config


def call(name, arguments):
    return component_mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": name, "arguments": arguments}})["result"]


def test_discovery_and_status_need_no_knowledge_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_CONFIG", str(tmp_path / "absent.json"))
    response = component_mcp.handle({"id": 1, "method": "tools/list"})
    for tool in response["result"]["tools"]:
        if tool["name"] == "mindie_status":
            result = call(tool["name"], {})
        else:
            arguments = {"argv": ["--help"]} if "argv" in tool["inputSchema"]["required"] else {}
            if tool["name"] == "diagnostics_bundle":
                arguments["root"] = str(tmp_path)
            result = call(tool["name"], arguments)
        assert result["isError"] is (tool["name"] != "mindie_status")
    assert not (tmp_path / "data/admission.sqlite3").exists()


def test_real_profiler_inventory_and_cli_error(installed, tmp_path):
    capture = ROOT / "tests/fixtures/profiler/rank_0"
    inventory = call("profiler", {"argv": ["inventory", "--root", str(capture)]})
    assert not inventory["isError"], inventory
    assert inventory["structuredContent"]["result"]["rank_count"] == 1
    assert inventory["structuredContent"]["result"]["ranks"][0]["files"]
    error = call("profiler", {"argv": ["does-not-exist"]})
    assert error["isError"]
    assert error["structuredContent"]["exit_code"] == 2
    assert "unknown command 'does-not-exist'" in error["structuredContent"]["stderr"]


def test_real_sim_help_and_invalid_command(installed, tmp_path, monkeypatch):
    shadow = tmp_path / "ambient" / "mindie_sim"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text("raise RuntimeError('ambient source shadowed installed runtime')\n")
    monkeypatch.setenv("PYTHONPATH", str(shadow.parent))
    help_result = call("sim", {"argv": ["--help"]})
    assert not help_result["isError"], help_result
    assert "predict" in help_result["structuredContent"]["stdout"]
    missing = call("sim", {"argv": ["invalid-component-operation"]})
    assert missing["isError"]
    assert missing["structuredContent"]["exit_code"] == 2
    assert "invalid-component-operation" in missing["structuredContent"]["stderr"]


def test_runtime_scripts_directory_precedes_path(installed, tmp_path, monkeypatch):
    executable = component_tools.command(json.loads(installed.read_text()), "sim")[0]
    python_dir = str(Path(sys.executable).parent)
    scripts_dir = str(tmp_path / "python-scripts")
    monkeypatch.setattr(component_tools.sysconfig, "get_path", lambda key: scripts_dir)
    lookups = []

    def which(name, *, path=None):
        lookups.append(path)
        # Model the global-Python scripts layout using the real installed CLI.
        return executable if path == scripts_dir else None

    monkeypatch.setattr(component_tools.shutil, "which", which)
    result = call("sim", {"argv": ["--help"]})
    assert not result["isError"], result
    assert "predict" in result["structuredContent"]["stdout"]
    assert lookups == [python_dir, scripts_dir]


def test_real_profiler_relative_output_uses_explicit_workdir(installed, tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    result = call("profiler", {"argv": ["inventory", "--root", str(ROOT / "tests/fixtures/profiler/rank_0"), "--output", "result.json"],
                          "cwd": str(workdir)})
    assert not result["isError"], result
    assert json.loads((workdir / "result.json").read_text())["rank_count"] == 1
    assert not (tmp_path / "result.json").exists()


def test_real_coordinator_inventory_and_local_diagnostics(installed, tmp_path):
    hosts = call("coordinator", {"argv": ["hosts", "--action", "list"]})
    assert not hosts["isError"], hosts
    assert hosts["structuredContent"]["result"]["outcome"] == "success"
    status = call("diagnostics_status", {})
    assert not status["isError"], status
    assert status["structuredContent"]["result"]["status"] == "not_configured"
    logs = tmp_path / "logs"
    logs.mkdir()
    bundle = call("diagnostics_bundle", {"root": str(logs), "output": str(tmp_path / "support.zip")})
    assert not bundle["isError"], bundle
    assert (tmp_path / "support.zip").is_file()


def test_completed_large_output_keeps_cli_failure_and_both_streams(installed):
    value = json.loads(installed.read_text())
    value["component_commands"] = {"sim": [sys.executable, "-c",
        "import sys,time; print('x'*10000,flush=True); time.sleep(0.15); "
        "print('business failure',file=sys.stderr); sys.exit(3)"]}
    installed.write_text(json.dumps(value))
    result = call("sim", {"argv": [], "max_output_tokens": 256})
    assert result["isError"]
    assert result["structuredContent"]["status"] == "completed"
    assert result["structuredContent"]["exit_code"] == 3
    assert "business failure" in result["structuredContent"]["stderr"]
    assert result["structuredContent"]["output_truncated"]
    assert len(result["structuredContent"]["stdout"]) + len(result["structuredContent"]["stderr"]) <= 1024


def test_running_cli_stops_at_capture_limit(installed):
    value = json.loads(installed.read_text())
    value["component_commands"] = {"sim": [sys.executable, "-c",
        "import sys,time; sys.stdout.write('x'*(2*1024*1024)); "
        "sys.stdout.flush(); time.sleep(30)"]}
    installed.write_text(json.dumps(value))
    result = call("sim", {"argv": [], "max_output_tokens": 256, "timeout_ms": 2000})
    assert result["isError"]
    result = result["structuredContent"]
    assert result["status"] == "output_limit"
    assert result["exit_code"] is not None
    assert result["output_truncated"]
    assert not result["automatic_retry"]
    assert len(result["stdout"]) + len(result["stderr"]) <= 1024


def test_stdio_cancel_interrupts_owned_process_and_allows_next_call(installed, tmp_path):
    marker = tmp_path / "started"
    value = json.loads(installed.read_text())
    value["component_commands"] = {"sim": [sys.executable, "-c",
        "from pathlib import Path; import os,time; Path(" + repr(str(marker)) +
        ").write_text(str(os.getpid())); time.sleep(30)"]}
    installed.write_text(json.dumps(value))
    process = subprocess.Popen([sys.executable, str(ROOT / "scripts/component_mcp.py")],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=dict(os.environ))
    received = queue.Queue()
    reader = threading.Thread(target=lambda: [received.put(json.loads(line)) for line in process.stdout],
                              daemon=True)
    reader.start()

    def send(message):
        process.stdin.write(json.dumps(message) + "\n")
        process.stdin.flush()

    try:
        send({"id": 1, "method": "tools/call", "params": {"name": "sim", "arguments": {"argv": []}}})
        send({"id": 2, "method": "ping"})
        assert received.get(timeout=5)["id"] == 2
        deadline = time.monotonic() + 3
        while not marker.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert int(marker.read_text()) > 0
        send({"method": "notifications/cancelled", "params": {"requestId": 1}})
        cancelled = received.get(timeout=8)
        assert cancelled["id"] == 1
        assert cancelled["result"]["isError"]
        assert cancelled["result"]["structuredContent"]["status"] == "cancelled"
        send({"id": 3, "method": "tools/call", "params": {"name": "mindie_status", "arguments": {}}})
        assert received.get(timeout=5)["result"]["structuredContent"]["status"] == "configured"
        process.stdin.close()
        assert process.wait(timeout=8) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdout, process.stderr):
            stream.close()


def test_launcher_registers_and_calls_through_native_dsh_mcp_client(installed, tmp_path):
    modules = ROOT / "node_modules"
    if not (modules / "@deepseek-ai/dsh-mcp-client/package.json").is_file():
        executable = shutil.which("dsh")
        assert executable, "Install the package.json pinned DSH version before native contracts"
        modules = Path(executable).resolve().parents[1] / "node_modules"
    assert (modules / "@deepseek-ai/dsh-mcp-client/package.json").is_file()
    result = subprocess.run(
        ["node", str(ROOT / "tests/component_native_harness.mjs")],
        capture_output=True, text=True, timeout=15, check=True,
        env={**os.environ, "COMPONENT_PACKAGE": str(ROOT), "DSH_MODULE_ROOT": str(modules),
             "COMPONENT_PYTHON": sys.executable,
             "COMPONENT_CAPTURE": str(ROOT / "tests/fixtures/profiler/rank_0")},
    )
    value = json.loads(result.stdout)
    assert value["rank_count"] == 1
    assert value["status"] == "configured"


@pytest.fixture
def sim_gate(installed):
    # The new model contract may be tested before its independent release;
    # an explicit installed interpreter never replaces the plugin runtime.
    runtime = os.environ.get("MINDIE_SIM_CONTRACT_PYTHON")
    if runtime is None:
        import importlib.metadata
        from packaging.version import Version
        if Version(importlib.metadata.version("mindie-sim")) < Version("0.8.0"):
            pytest.skip("new sim contract requires MINDIE_SIM_CONTRACT_PYTHON (sim >= 0.8)")
        runtime = sys.executable
    value = json.loads(installed.read_text())
    value.setdefault("component_commands", {})["sim"] = [runtime, "-I", "-m", "mindie_sim"]
    installed.write_text(json.dumps(value))
    return call


@pytest.fixture
def sim_case(tmp_path):
    path = tmp_path / "case.json"
    path.write_text(json.dumps({
        "machine": {
            "name": "synthetic-cli-control", "num_devices": 1,
            "device": {"memory_bytes": 1024**3, "compute_units": 4,
                       "compute_peaks": {"bf16": 16e9, "fp32": 8e9},
                       "rates": {"memory": 8e9, "vector": 4e9}},
        },
        "model": {"name": "control", "layers": 1, "hidden_size": 32,
                  "num_heads": 4, "num_kv_heads": 2, "head_dim": 8,
                  "intermediate_size": 64, "vocab_size": 128},
        "workload": {"requests": [{"id": "r", "prompt_tokens": 4, "output_tokens": 2}]},
        "runtime": {"max_num_batched_tokens": 8, "max_model_len": 128, "graph_mode": "none"},
    }))
    return path


def test_sim_installed_cli_keeps_operator_graph_and_serving_boundaries(sim_gate, sim_case, tmp_path):
    operators = sim_gate("sim", {"argv": [
        "operators", str(sim_case), "--phase", "prefill", "--tokens", "4", "--scope", "model",
    ]})
    assert not operators["isError"], operators
    isolated = operators["structuredContent"]["result"]
    assert isolated["boundary"] == "sum_of_isolated_device_programs"
    graph_path = tmp_path / "graph.json"
    graph = sim_gate("sim", {"argv": [
        "graph", str(sim_case), "--phase", "prefill", "--tokens", "4",
        "--scope", "model", "--output", str(graph_path),
    ]})
    assert not graph["isError"], graph
    assert graph["structuredContent"]["stdout"] == ""
    result = json.loads(graph_path.read_text())
    assert result["boundary"] == "device_graph"
    assert result["operator_occurrences"] == isolated["operator_occurrences"]
    assert result["isolated"]["operator_sum_ms"] == pytest.approx(isolated["operator_sum_ms"])
    prediction_path = tmp_path / "prediction.json"
    serving = sim_gate("sim", {"argv": [
        "predict", str(sim_case), "--output", str(prediction_path),
    ]})
    assert not serving["isError"], serving
    prediction = json.loads(prediction_path.read_text())
    assert prediction["metric_boundary"] == "device_token_commit"
    assert prediction["requests"][0]["generated_tokens"] == 2


def test_sim_preserves_real_cli_error_and_exit_status(sim_gate, tmp_path):
    bad = sim_gate("sim", {"argv": [
        "graph", str(tmp_path / "absent.json"), "--phase", "prefill", "--tokens", "4",
    ]})
    assert bad["isError"]
    value = bad["structuredContent"]
    assert value["exit_code"] == 2
    assert value["stdout"] == ""
    error = json.loads(value["stderr"])
    assert error["status"] == "failed"
    assert str(tmp_path / "absent.json") in error["error"] or repr(str(tmp_path / "absent.json")) in error["error"]


def test_sim_partial_study_keeps_success_and_failure_in_the_nonzero_cli_result(
    sim_gate, sim_case, tmp_path,
):
    manifest = tmp_path / "study.json"
    manifest.write_text(json.dumps({
        "candidates": [
            {"id": "ordinary", "case": sim_case.name},
            {"id": "unavailable", "case": sim_case.name, "overrides": {
                "runtime": {"kernels": {"lowerings": {
                    "gqa_attention": "unavailable_source_implementation",
                }}},
            }},
        ],
        "probes": [{"id": "prefill", "phase": "prefill", "tokens": 4}],
    }))
    response = sim_gate("sim", {"argv": ["study", str(manifest)]})
    assert response["isError"]
    value = response["structuredContent"]
    assert value["exit_code"] == 2
    report = value["result"]
    assert report["status"] == "partial"
    evaluations = {row["candidate"]: row for row in report["evaluations"]}
    assert evaluations["ordinary"]["status"] == "modeled"
    assert evaluations["ordinary"]["operators"]["operator_sum_ms"] > 0
    assert evaluations["unavailable"]["status"] == "failed"
    assert "unavailable_source_implementation" in evaluations["unavailable"]["error"]
    assert {row["candidate"] for ranking in report["rankings"]
            for row in ranking["candidates"]} == {"ordinary"}
