"""Both native host and stdio knowledge paths are default-enabled and honor explicit deactivation."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from mindie_knowledge.loop.activation import Admission

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import knowledge_mcp
import mindie_sidecar


def test_stdio_discovery_and_session_fallback_work_without_admission_db(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    monkeypatch.delenv("MINDIE_DSH_SESSION_ID", raising=False)
    tools = knowledge_mcp.handle({"id": 1, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == [
        "knowledge_query",
        "knowledge_explain",
        "knowledge_distill",
        "knowledge_feedback",
        "knowledge_sync",
    ]
    for tool in tools:
        assert "/mindie-agent" not in tool["description"]
    assert not (tmp_path / "admission.sqlite3").exists()
    fallback_id = knowledge_mcp.session_identity()
    assert fallback_id.startswith("dsh-mcp-")

    # activation_token auto-provisions a not_before=0.0 lease on demand and upgrades automatic rows
    token = knowledge_mcp.activation_token(fallback_id, project_root=str(tmp_path))
    gate = Admission(tmp_path / "admission.sqlite3")
    lease = gate.knowledge_lease(fallback_id)
    assert lease is not None and lease["token"] == token
    assert lease["activated_at"] == 0.0

    gate.associate("auto-session", project_root=str(tmp_path), not_before=1.0)
    upgraded = knowledge_mcp.activation_token("auto-session", project_root=str(tmp_path))
    assert gate.knowledge_lease("auto-session")["token"] == upgraded


@pytest.mark.parametrize("name,arguments", [
    ("knowledge_query", {"query": "KDA"}),
    ("knowledge_explain", {"ref": "mindie://test/units/kda@1"}),
    ("knowledge_distill", {"inputs": [], "task_state": "in_flight"}),
    ("knowledge_feedback", {"ref": "mindie://test/units/kda@1", "rating": "up"}),
    ("knowledge_sync", {}),
])
def test_stdio_deactivated_session_denial_precedes_connection(tmp_path, monkeypatch, name, arguments):
    monkeypatch.setenv("MINDIE_DSH_SESSION_ID", "revoked-session")
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    gate = Admission(tmp_path / "admission.sqlite3")
    gate.activate("revoked-session", project_root=str(tmp_path))
    gate.deactivate("revoked-session")
    with patch.object(knowledge_mcp, "knowledge_connection") as connect:
        with pytest.raises(RuntimeError, match="not activated"):
            knowledge_mcp.call_knowledge(name, arguments, {})
        connect.assert_not_called()


def test_sidecar_rechecks_deactivated_or_forged_token_before_any_rpc(tmp_path, monkeypatch):
    monkeypatch.setenv("MINDIE_DSH_DATA_HOME", str(tmp_path))
    gate = Admission(tmp_path / "admission.sqlite3")
    lease = gate.activate("session", project_root=str(tmp_path))
    with patch.object(mindie_sidecar, "connection") as connect:
        with pytest.raises(mindie_sidecar.SidecarError, match="invalid session token"):
            mindie_sidecar.op_rpc(dict(session="session", token="forged", method="query", arguments={"query": "x"}))
        gate.deactivate("session")
        for method in ("query", "explain", "feedback", "distill"):
            with pytest.raises(mindie_sidecar.SidecarError, match="not activated"):
                mindie_sidecar.op_rpc(dict(session="session", method=method, arguments={}))
            with pytest.raises(mindie_sidecar.SidecarError, match="not activated"):
                mindie_sidecar.op_rpc(dict(session="session", token=lease["token"], method=method, arguments={}))
        with pytest.raises(mindie_sidecar.SidecarError, match="not activated"):
            mindie_sidecar.op_sync(dict(session="session"))
        connect.assert_not_called()
