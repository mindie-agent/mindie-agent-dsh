"""Exercise real local Git feed refreshes through both DSH knowledge surfaces.

[Role] Verify native host and stdio sync routing, admission and failure receipts.
[Invariants] Use installed knowledge CLI and real feeds, stores and leases.
[Timeouts & Locks] Git setup and each local host/stdio call are bounded at 15s.
[Side-Effect Policy] Only test-owned temporary Git repositories and knowledge caches.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from mindie_knowledge.loop.activation import Admission
from mindie_knowledge.loop.store import Store
from mindie_knowledge.publication_contract import GRADED_SCHEMA, make_contract, render_contract

from domain_harness_support import UNIT_ID, graded_unit

ROOT = Path(__file__).resolve().parents[1]
SESSION = "sync-session"
DOMAIN = "operators"


@pytest.fixture(scope="session")
def published_feed(tmp_path_factory):
    repository = tmp_path_factory.mktemp("dsh-feed")
    unit = graded_unit()
    files = {
        "publication-contract.json": render_contract(make_contract(DOMAIN, "a" * 40, schema=GRADED_SCHEMA)),
        unit.path: unit.render(),
    }
    for name, text in files.items():
        destination = repository / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(text.encode("utf-8"))
    for args in (["init", "-q", "-b", "main"], ["add", "."], ["commit", "-qm", "local feed"]):
        subprocess.run(
            ["git", "-C", str(repository), "-c", "user.name=feed",
             "-c", "user.email=feed@example.invalid", "-c", "core.autocrlf=false", *args],
            capture_output=True, text=True, check=True, timeout=15,
        )
    return {"repository": "fixture/operators", "domain": DOMAIN,
            "url": repository.as_uri(), "ref": "main"}


@pytest.fixture
def runtime(tmp_path, monkeypatch, published_feed):
    default = tmp_path / "engine.json"
    target = tmp_path / "operators-engine.json"
    capture_config = {
        # Feed refresh never invokes the capture redactor or starts a service.
        "redactor_executable": str(tmp_path / "unused-redactor"),
        "transcript_adapter": str(ROOT / "scripts/dsh_transcript.py"),
    }
    default.write_text(json.dumps({
        **capture_config,
        "root": str(tmp_path / "knowledge"), "domain": "vllm-ascend", "feeds": [],
        "admission_path": str(tmp_path / "admission.sqlite3"),
    }))
    target.write_text(json.dumps({
        **capture_config,
        "root": str(tmp_path / "operators"), "domain": DOMAIN, "feeds": [published_feed],
        "admission_path": str(tmp_path / "operators-admission.sqlite3"),
    }))
    adapter = tmp_path / "adapter.json"
    adapter.write_text(json.dumps({
        "python": sys.executable, "venv_dir": sys.prefix, "version": "test",
        "engine_config": str(default), "state_dir": str(tmp_path / "state"),
        "domain_configs": {DOMAIN: str(target)},
    }))
    for key, value in {
        "MINDIE_DSH_CONFIG": adapter, "MINDIE_DSH_DATA_HOME": tmp_path,
        "MINDIE_DSH_ENGINE_CONFIG": default, "MINDIE_DSH_SESSION_ID": SESSION,
        "MINDIE_DSH_NO_REEXEC": "1",
    }.items():
        monkeypatch.setenv(key, str(value))
    return tmp_path, default, target


def invoke(channel, root, arguments):
    request = {"id": 1, "method": "tools/call",
               "params": {"name": "knowledge_sync", "arguments": arguments}}
    if channel == "stdio":
        completed = subprocess.run(
            [sys.executable, str(ROOT / "scripts/knowledge_mcp.py"), "knowledge"],
            input=json.dumps(request) + "\n", capture_output=True, text=True,
            check=True, timeout=15,
        )
        result = json.loads(completed.stdout)["result"]
        return result["isError"], result.get("structuredContent", {"message": result["content"][0]["text"]})
    completed = subprocess.run(
        ["node", str(ROOT / "tests/host_domain_harness.mjs")],
        capture_output=True, text=True, check=True, timeout=15,
        env={**os.environ, "DOMAIN_CWD": str(root), "DOMAIN_SESSION": SESSION,
             "DOMAIN_SKIP_ACTIVATION": "1", "DOMAIN_OPERATIONS": json.dumps([
                 {"tool": "knowledge_sync", "args": arguments},
             ])},
    )
    envelope = json.loads(completed.stdout)
    assert envelope["llmCalls"] == 0
    result = envelope["results"][0]
    if result["ok"]:
        return False, result["value"]
    text = result["error"]
    return True, json.loads(text) if text.startswith("{") else {"message": text}


def stored_ids(root):
    store = Store(root / "operators", DOMAIN)
    try:
        return [row["id"] for row in store.graded.query(
            "gradedfeedbackprobe", include_unknown=True,
        )["results"]]
    finally:
        store.close()


@pytest.mark.parametrize("channel", ["stdio", "native"])
def test_sync_requires_existing_manual_and_target_leases(runtime, channel):
    root, _default, _target = runtime
    error, denied = invoke(channel, root, {"domain": DOMAIN})
    assert error and "/mindie-agent" in denied["message"]
    assert not (root / "operators").exists()
    Admission(root / "admission.sqlite3").activate(SESSION, project_root=str(root))
    error, denied = invoke(channel, root, {"domain": DOMAIN})
    assert error and "no active admission lease" in denied["message"]
    assert not (root / "operators").exists()
    Admission(root / "operators-admission.sqlite3").activate(SESSION, project_root=str(root))
    error, result = invoke(channel, root, {"domain": DOMAIN})
    assert not error, result
    assert result["status"] == "ok"
    assert result["feeds"][0]["status"] == "synced"
    assert stored_ids(root) == [UNIT_ID]
    assert not (root / "knowledge").exists()


@pytest.mark.parametrize("channel", ["stdio", "native"])
def test_sync_failed_feed_keeps_success_receipt_and_snapshot(runtime, channel):
    root, _default, target = runtime
    Admission(root / "admission.sqlite3").activate(SESSION, project_root=str(root))
    Admission(root / "operators-admission.sqlite3").activate(SESSION, project_root=str(root))
    config = json.loads(target.read_text())
    missing = {"repository": "fixture/missing", "domain": DOMAIN,
               "url": (root / "missing.git").as_uri(), "ref": "main"}
    config["feeds"].append(missing)
    target.write_text(json.dumps(config))
    error, degraded = invoke(channel, root, {"domain": DOMAIN})
    assert error, degraded
    assert degraded["status"] == "degraded"
    assert [row["status"] for row in degraded["feeds"]] == ["synced", "unavailable"]
    assert degraded["feeds"][1]["detail"]
    assert stored_ids(root) == [UNIT_ID]

    config["feeds"] = [missing]
    target.write_text(json.dumps(config))
    error, failed = invoke(channel, root, {"domain": DOMAIN})
    assert error and failed["status"] == "sync_failed", failed
    assert failed["feeds"][0]["status"] == "unavailable"
    assert stored_ids(root) == [UNIT_ID]
