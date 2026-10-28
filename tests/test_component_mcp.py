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


def test_real_sim_help_and_missing_case(installed, tmp_path):
    help_result = call("sim", {"argv": ["--help"]})
    assert not help_result["isError"], help_result
    assert "predict" in help_result["structuredContent"]["stdout"]
    missing = call("sim", {"argv": ["predict", "--input", str(tmp_path / "absent.json")]})
    assert missing["isError"]
    assert missing["structuredContent"]["exit_code"] == 1
    assert "absent.json" in missing["structuredContent"]["result"]["reason"]
    assert missing["structuredContent"]["result"]["status"] == "error"


def test_real_sim_relative_output_uses_explicit_workdir(installed, tmp_path):
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    result = call("sim", {"argv": ["hardware", "--soc", "Ascend910B", "--output", "result.json"],
                          "cwd": str(workdir)})
    assert not result["isError"], result
    assert json.loads((workdir / "result.json").read_text())["command"] == "hardware"
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
        "import sys; print('x'*10000); print('business failure',file=sys.stderr); sys.exit(3)"]}
    installed.write_text(json.dumps(value))
    result = call("sim", {"argv": [], "max_output_tokens": 256})
    assert result["isError"]
    assert result["structuredContent"]["exit_code"] == 3
    assert "business failure" in result["structuredContent"]["stderr"]
    assert result["structuredContent"]["output_truncated"]
    assert len(result["structuredContent"]["stdout"]) + len(result["structuredContent"]["stderr"]) <= 1024


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
