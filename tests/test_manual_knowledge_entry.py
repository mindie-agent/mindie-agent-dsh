"""Both native host and stdio knowledge paths share manual session admission."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from mindie_knowledge.loop.activation import Admission

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge_mcp
import mindie_sidecar


@pytest.mark.parametrize("state", ["missing", "automatic", "other-session", "revoked"])
@pytest.mark.parametrize("name,arguments", [
    ("knowledge_query", {"query": "KDA"}),
    ("knowledge_explain", {"ref": "mindie://test/units/kda@1"}),
    ("knowledge_distill", {"inputs": [], "task_state": "in_flight"}),
    ("knowledge_sync", {}),
])
def test_stdio_knowledge_denial_precedes_connection(tmp_path, monkeypatch, state, name, arguments):
    monkeypatch.setenv("MINDIE_DSH_SESSION_ID", "session")
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    gate = Admission(tmp_path / "admission.sqlite3")
    if state == "automatic":
        gate.associate("session", project_root=str(tmp_path), not_before=1.0)
    elif state == "other-session":
        gate.activate("parent", project_root=str(tmp_path))
    elif state == "revoked":
        gate.activate("session", project_root=str(tmp_path))
        gate.deactivate("session")
    with patch.object(knowledge_mcp, "knowledge_connection") as connect:
        with pytest.raises(RuntimeError, match="/mindie-agent"):
            knowledge_mcp.call_knowledge(name, arguments, {})
        connect.assert_not_called()


def test_sidecar_rechecks_default_route_token_before_any_rpc(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    gate = Admission(tmp_path / "admission.sqlite3")
    lease = gate.activate("session", project_root=str(tmp_path))
    gate.deactivate("session")
    with patch.object(mindie_sidecar, "connection") as connect:
        for method in ("query", "explain", "feedback", "distill"):
            with pytest.raises(mindie_sidecar.SidecarError, match="/mindie-agent"):
                mindie_sidecar.op_rpc(dict(session="session", token=lease["token"], method=method, arguments={}))
        with pytest.raises(mindie_sidecar.SidecarError, match="/mindie-agent"):
            mindie_sidecar.op_sync(dict(session="session", token=lease["token"]))
        connect.assert_not_called()


def test_stdio_discovery_follows_only_its_native_session_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("MINDIE_DSH_SESSION_ID", raising=False)
    request = {"id": 1, "method": "tools/list"}
    assert knowledge_mcp.handle(request)["result"]["tools"] == []
    assert not (tmp_path / "admission.sqlite3").exists()
    gate = Admission(tmp_path / "admission.sqlite3")
    gate.activate("parent", project_root=str(tmp_path))
    monkeypatch.setenv("MINDIE_DSH_SESSION_ID", "child")
    assert knowledge_mcp.handle(request)["result"]["tools"] == []
    gate.activate("child", project_root=str(tmp_path))
    assert knowledge_mcp.handle(request)["result"]["tools"]
    gate.deactivate("child")
    assert knowledge_mcp.handle(request)["result"]["tools"] == []
    with patch.object(knowledge_mcp, "knowledge_connection") as connect:
        with pytest.raises(RuntimeError, match="/mindie-agent"):
            knowledge_mcp.call_knowledge("knowledge_sync", {}, {})
        connect.assert_not_called()


def test_stdio_discovery_reports_corrupt_admission_instead_of_an_empty_catalog(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("MINDIE_DSH_SESSION_ID", "session")
    (tmp_path / "admission.sqlite3").write_bytes(b"broken sqlite database")
    result = knowledge_mcp.handle({"id": 1, "method": "tools/list"})
    assert result["error"]["code"] == -32000
    assert "database" in result["error"]["message"].lower()
