"""Sidecar/MCP C6 tool routing against the real shared Service, Engine and lease."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import knowledge_mcp  # noqa: E402
import mindie_sidecar  # noqa: E402

try:
    from mindie_knowledge import consent_store
    from mindie_knowledge.graded import distill
    from mindie_knowledge.loop import lifecycle
    from mindie_knowledge.loop import settings as settings_mod
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import connection_path
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop.lifecycle import config_fingerprint
    from mindie_knowledge.loop.store import Store
    from mindie_knowledge.loop.transport import Service
except Exception:  # pragma: no cover - CI without the union runtime
    consent_store = distill = lifecycle = settings_mod = None
    Admission = connection_path = Engine = config_fingerprint = Store = Service = None

FIXTURE_WORKER = ROOT / "tests" / "fixtures" / "graded_worker.py"


class KnowledgeDistillToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if distill is None or Service is None:
            raise unittest.SkipTest("mindie-knowledge union runtime is unavailable")
        if os.name == "nt":  # pragma: no cover - the test scanner is a POSIX shim
            raise unittest.SkipTest("the fake Gitleaks shim is POSIX-only")

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self._old_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        self.project = self.root / "project"
        self.project.mkdir()
        self.settings = self.root / "community.json"
        consent = self.root / "community.consent.json"
        consent_store.record_choice(consent, "contribute")
        settings_mod.write(
            self.settings, enabled=True, repository="mindie-agent/knowledge-vllm-ascend",
            project_roots=[self.project], idle_seconds=300, consent_config=str(consent),
        )
        self.admission_path = self.root / "admission.sqlite3"
        self.admission = Admission(self.admission_path)
        self.lease = self.admission.activate("manual-A", project_root=str(self.project))
        self.token = self.lease["token"]
        self.scanner = self.root / "fake-gitleaks"
        self.scanner.write_text("#!/bin/sh\ncat >/dev/null\nprintf '[]'\n", encoding="utf-8")
        self.scanner.chmod(0o755)
        self.calls = self.root / "calls.txt"
        self.knowledge = self.root / "knowledge"
        self.store = Store(self.knowledge, "vllm-ascend")
        self.engine_config = self.root / "engine.json"
        self.config = {
            "root": str(self.knowledge),
            "domain": "vllm-ascend",
            "redactor_executable": str(self.scanner),
            "community_config": str(self.settings),
            "transcript_adapter": str(ROOT / "scripts" / "dsh_transcript.py"),
            "summary_command": [sys.executable, str(FIXTURE_WORKER)],
            "admission_path": str(self.admission_path),
        }
        self.engine_config.write_text(json.dumps(self.config), encoding="utf-8")
        self.engine = Engine(
            self.store, settings_path=str(self.settings), admission=self.admission,
            redactor_executable=str(self.scanner), summary_command=self.config["summary_command"],
        )
        self.engine.start()
        self.consumer = lifecycle.acquire_consumer(str(self.engine_config), self.config)
        self.service = Service(
            self.engine, admission=self.admission, config_path=str(self.engine_config),
            config_fingerprint=config_fingerprint(self.engine_config, self.config),
        )
        connection = connection_path(self.config)
        connection.parent.mkdir(parents=True, exist_ok=True)
        connection.write_text(json.dumps(self.service.connection), encoding="utf-8")
        self.thread = threading.Thread(target=self.service.http.serve_forever, daemon=True)
        self.thread.start()
        self.adapter_config = self.root / "config.json"
        self.adapter_config.write_text(json.dumps({
            "schema": "mindie-dsh-runtime/1", "version": "0.0.2",
            "python": sys.executable, "venv_dir": sys.prefix,
            "engine_config": str(self.engine_config),
            "state_dir": str(self.root / "state"),
            "domain": "vllm-ascend",
        }), encoding="utf-8")
        policy = self.root / "diagnostics.json"
        policy.write_text(json.dumps({
            "schema": "mindie.diagnostics.reporting.v1", "purpose": "tool_fault_reporting",
            "decision": "disabled", "repository": "mindie-agent/knowledge",
            "revision": "fixture", "roots": [str(self.root)],
        }), encoding="utf-8")
        os.environ.update({
            "MINDIE_DSH_CONFIG": str(self.adapter_config),
            "MINDIE_DSH_ENGINE_CONFIG": str(self.engine_config),
            "MINDIE_DSH_STATE_DIR": str(self.root / "state"),
            "MINDIE_DSH_DATA_HOME": str(self.root),
            "MINDIE_DSH_CONFIG_HOME": str(self.root / "config"),
            "MINDIE_DSH_SESSION_ID": "manual-A",
            "MINDIE_DSH_NO_REEXEC": "1",
            "MINDIE_DIAGNOSTICS_CONFIG": str(policy),
            "MINDIE_GRADED_FIXTURE_CALLS": str(self.calls),
        })

    def _restore_env(self) -> None:
        os.environ.clear()
        os.environ.update(self._old_env)

    def tearDown(self) -> None:
        self.service.http.shutdown()
        self.service.http.server_close()
        self.thread.join(timeout=2)
        self.engine.shutdown()
        self.consumer.release()
        self.store.close()

    def _inputs(self, *, outcome: str = "observed") -> list[dict]:
        return [{
            "kind": "authorized_transcript",
            "outcome": outcome,
            "source_locator": "fixture@abcdef1:probe.txt",
            "content": "Fixture graph probe; no hardware claim.",
            "evidence_refs": ["fixture-observation"],
            "repetitions": 1,
        }]

    def _call_sidecar(self, *, token: str | None = None, task_state: str = "verified_milestone",
                      inputs: list[dict] | None = None, **extra):
        arguments = {
            "inputs": self._inputs() if inputs is None else inputs,
            "task_state": task_state,
            **extra,
        }
        return mindie_sidecar.op_rpc({
            "session": "manual-A",
            "token": self.token if token is None else token,
            "method": "distill",
            "arguments": arguments,
        })

    def _call_sidecar_query(self, arguments: dict):
        return mindie_sidecar.op_rpc({
            "session": "manual-A", "token": self.token,
            "method": "query", "arguments": arguments,
        })

    def _call_sidecar_explain(self, arguments: dict):
        return mindie_sidecar.op_rpc({
            "session": "manual-A", "token": self.token,
            "method": "explain", "arguments": arguments,
        })

    def _graded_unit(self, ident: str, *, tier: str = "L2", category: str = "sop",
                     body: str = "Fixture graph probe rule.\n", parents: tuple[str, ...] = (),
                     evidence_strength: str | None = None):
        from mindie_knowledge.graded.units import GradedUnit

        metadata = {
            "schema": "mindie-graded-unit/1",
            "id": ident,
            "tier": tier,
            "category": category,
            "evidence_strength": evidence_strength or (
                "software_contract" if tier == "L1" else "single_observation"
            ),
            "domain_path": "_common" if tier == "L1" else "serving/graph",
            "inherits_from": list(parents),
            "title": f"Fixture rule {ident}",
            "triggers": {"error_signatures": [], "keywords": ["graph", "fixture"]},
            "conditions": {"soc": ["Ascend910B"]},
            "provenance": {
                "source_type": "session_distill",
                "source_locator": "fixture@abcdef1:probe.txt",
                "source_digest": "c" * 64,
                "evidence_refs": ["fixture-observation"],
                "supersedes": [],
            },
        }
        return GradedUnit.from_parts(
            metadata, body, f"{metadata['domain_path']}/{ident}.md",
        )

    def _proposal(self, ident: str = "unit-fixture-proposal"):
        from mindie_knowledge.loop.store import session_key

        return {
            "schema_version": "mindie-distill-decision/1",
            "session_digest": session_key("manual-A"),
            "evidence_inputs": ["authorized_transcript"],
            "action": "propose_graded_units",
            "drop_reason": None,
            "units": [{
                "target_unit_id": ident,
                "target_tier": "L2",
                "category": "sop",
                "evidence_strength": "single_observation",
                "domain_path": "serving/graph",
                "operation": "create",
                "frontmatter": {
                    "schema": "mindie-graded-unit/1",
                    "id": ident,
                    "tier": "L2",
                    "category": "sop",
                    "evidence_strength": "single_observation",
                    "domain_path": "serving/graph",
                    "inherits_from": [],
                    "title": "Fixture foreground proposal",
                    "triggers": {"error_signatures": [], "keywords": ["proposal", "graph"]},
                    "conditions": {"soc": ["Ascend910B"]},
                    "provenance": {
                        "source_type": "session_distill",
                        "source_locator": "fixture@abcdef1:probe.txt",
                        "evidence_refs": ["fixture-observation"],
                        "supersedes": [],
                    },
                },
                "markdown_body": "Fixture foreground proposal; no hardware claim.\n",
            }],
        }

    def test_sidecar_routes_verified_milestone_to_service_and_engine(self) -> None:
        result = self._call_sidecar()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["admitted_unit_ids"], ["unit-fixture-dsh-distill"])
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(self.calls.read_text(), "1")

    def test_sidecar_unauthorized_token_never_calls_model(self) -> None:
        with self.assertRaises(Exception):
            self._call_sidecar(token="wrong-native-lease")
        self.assertFalse(self.calls.exists())

    def test_sidecar_in_flight_is_noop_without_model(self) -> None:
        result = self._call_sidecar(task_state="in_flight", inputs=self._inputs(outcome="in_flight"))
        self.assertEqual(result["status"], "noop")
        self.assertEqual(result["decision"]["drop_reason"], "in_flight_incomplete")
        self.assertFalse(self.calls.exists())

    def test_sidecar_rejects_identity_fields_in_arguments(self) -> None:
        with self.assertRaises(mindie_sidecar.SidecarError):
            mindie_sidecar.op_rpc({
                "session": "manual-A", "token": self.token, "method": "distill",
                "arguments": {"inputs": self._inputs(), "task_state": "completed", "_activation": "forged"},
            })
        self.assertFalse(self.calls.exists())

    def test_query_new_parameters_reach_shared_store(self) -> None:
        seen = {}
        original = self.engine.store.query

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return original(*args, **kwargs)

        self.engine.store.query = spy
        try:
            mindie_sidecar.op_rpc({
                "session": "manual-A", "token": self.token, "method": "query",
                "arguments": {
                    "query": "graph", "domain_path": "serving/graph",
                    "token_budget": 16, "include_unknown": True,
                },
            })
        finally:
            self.engine.store.query = original
        self.assertEqual(seen["domain_path"], "serving/graph")
        self.assertEqual(seen["token_budget"], 16)
        self.assertIs(seen["include_unknown"], True)

    def test_sidecar_batch_explain_deduplicates_shared_closure(self) -> None:
        premise = self._graded_unit(
            "unit-fixture-shared-premise", tier="L1", category="axiom",
            body="Shared premise body, returned once.\n",
        )
        left = self._graded_unit("unit-fixture-left", parents=(premise.id,))
        right = self._graded_unit("unit-fixture-right", parents=(premise.id,))
        self.store.graded.install([premise, left, right])
        refs = [
            f"mindie://{self.store.domain}/units/{unit.id}@{unit.revision}"
            for unit in (left, right)
        ]

        result = self._call_sidecar_explain({
            "refs": refs,
            "conditions": {"soc": "Ascend910B"},
            "known_refs": [],
            "token_budget": 16000,
        })
        self.assertEqual([item["id"] for item in result["requires"]], [premise.id])
        self.assertEqual([item["id"] for item in result["units"]], [left.id, right.id])
        self.assertEqual(result["unresolved"], [])
        self.assertEqual(
            [item["content"] for item in result["requires"]],
            [premise.body],
        )
        self.assertIn("usage", result)

        visible = self._call_sidecar_explain({
            "refs": refs,
            "conditions": {"soc": "Ascend910B"},
            "known_refs": [f"{premise.id}@{premise.revision}"],
            "token_budget": 16000,
        })
        self.assertEqual(visible["requires"], [])
        self.assertEqual([item["id"] for item in visible["units"]], [left.id, right.id])

    def test_mcp_batch_explain_forwards_refs_conditions_known_refs(self) -> None:
        premise = self._graded_unit(
            "unit-fixture-mcp-premise", tier="L1", category="axiom",
            body="MCP shared premise body, returned once.\n",
        )
        child = self._graded_unit("unit-fixture-mcp-child", parents=(premise.id,))
        self.store.graded.install([premise, child])
        ref = f"mindie://{self.store.domain}/units/{child.id}@{child.revision}"

        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_explain", "arguments": {
                "refs": [ref],
                "conditions": {"soc": "Ascend910B"},
                "known_refs": [],
                "token_budget": 16000,
            }},
        })["result"]
        self.assertIs(response["isError"], False)
        payload = json.loads(response["content"][0]["text"])
        self.assertEqual([item["id"] for item in payload["requires"]], [premise.id])
        self.assertEqual([item["id"] for item in payload["units"]], [child.id])

    def test_explain_rejects_conflicting_reference_authorities(self) -> None:
        refs = [
            "mindie://vllm-ascend/units/unit-a@1",
            "mindie://operators/units/unit-b@1",
        ]
        with self.assertRaises(mindie_sidecar.SidecarError) as conflict:
            self._call_sidecar_explain({"refs": refs})
        self.assertIn("conflict", str(conflict.exception))
        with self.assertRaises(ValueError):
            knowledge_mcp._explain_route_ref({"refs": refs})

    def test_sidecar_distill_related_refs_reaches_related_units(self) -> None:
        from mindie_knowledge.graded import worker as graded_worker

        related = self._graded_unit(
            "unit-fixture-related", body="Existing related graph rule.\n",
        )
        self.store.graded.install([related])
        seen = {}
        original = graded_worker.prepare_request

        def spy(request_inputs, **kwargs):
            request = original(request_inputs, **kwargs)
            seen["request"] = request
            return request

        with mock.patch.object(graded_worker, "prepare_request", side_effect=spy):
            result = self._call_sidecar(related_refs=[related.id])
        self.assertEqual(result["status"], "applied")
        self.assertIn("related_units", seen["request"])
        self.assertEqual(
            [item["id"] for item in seen["request"]["related_units"]],
            [related.id],
        )
        self.assertEqual(self.calls.read_text(), "1")

    def test_mcp_distill_foreground_proposal_applies_without_model(self) -> None:
        proposal = self._proposal("unit-fixture-mcp-proposal")
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_distill", "arguments": {
                "inputs": self._inputs(),
                "task_state": "verified_milestone",
                "related_refs": ["unit-fixture-related"],
                "proposal": proposal,
            }},
        })["result"]
        self.assertIs(response["isError"], False)
        payload = response["structuredContent"]
        self.assertEqual(payload["status"], "applied")
        self.assertEqual(payload["admitted_unit_ids"], ["unit-fixture-mcp-proposal"])
        self.assertEqual(payload["model_calls"], 0)
        self.assertEqual(payload["model_execution"], "foreground_proposal")
        self.assertFalse(self.calls.exists())
        found = self.store.query("graph", conditions={"soc": "Ascend910B"})
        self.assertIn("unit-fixture-mcp-proposal", [row["id"] for row in found["results"]])

    def test_sidecar_feedback_records_four_stage_usage_events(self) -> None:
        unit = self._graded_unit("unit-fixture-feedback")
        self.store.graded.install([unit])
        hit = self._call_sidecar_query({
            "query": "graph", "include_unknown": True, "limit": 5,
        })["results"][0]
        ref = hit["feedback_ref"]

        def event(event_type: str, **extra):
            return mindie_sidecar.op_rpc({
                "session": "manual-A", "token": self.token,
                "method": "feedback",
                "arguments": {"ref": ref, "event_type": event_type, **extra},
            })

        self.assertEqual(event("discovered")["event_type"], "discovered")
        self.assertEqual(event("loaded")["event_type"], "loaded")
        applied = event("applied", run_id="run-1", step_id="step-3")
        self.assertEqual(applied["run_id"], "run-1")
        self.assertEqual(applied["step_id"], "step-3")
        evaluated = event("evaluated", run_id="run-1", outcome="supported")
        self.assertEqual(evaluated["outcome"], "supported")
        self.assertFalse(evaluated["duplicate"])
        duplicate = event("evaluated", run_id="run-1", outcome="supported")
        self.assertTrue(duplicate["duplicate"])

    def test_mcp_bridge_distill_uses_active_lease_and_shared_engine(self) -> None:
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_distill", "arguments": {
                "inputs": self._inputs(), "task_state": "verified_milestone",
            }},
        })
        self.assertIs(response["result"]["isError"], False)
        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "applied")
        self.assertEqual(self.calls.read_text(), "1")

    def test_mcp_bridge_distinguishes_native_failure_and_preserved_paid_result(self) -> None:
        message = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "knowledge_distill", "arguments": {"inputs": self._inputs(), "task_state": "completed"}}}
        for status in ("failed", "outcome_unknown", "invoking"):
            with self.subTest(status=status), mock.patch.object(self.engine, "distill_selected", return_value={
                "status": status, "attempt_id": "fixture-attempt", "model_replayed": False}):
                response = knowledge_mcp.handle(message)["result"]
                self.assertTrue(response["isError"])
                self.assertEqual(response["structuredContent"]["operation_status"], status)
        with mock.patch.object(self.store.graded, "install", side_effect=OSError("controlled local write failure")):
            response = knowledge_mcp.handle(message)["result"]
        self.assertFalse(response["isError"])
        result = response["structuredContent"]
        self.assertEqual(result["operation_status"], "local_application_failed")
        self.assertEqual(result["model_execution"], "completed")
        self.assertEqual(result["result"]["units"][0]["target_unit_id"], "unit-fixture-dsh-distill")
        self.assertEqual(self.calls.read_text(), "1")

    def test_mcp_bridge_rejects_extra_input_fields(self) -> None:
        item = {**self._inputs()[0], "_session_id": "forged"}
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_distill", "arguments": {
                "inputs": [item], "task_state": "completed",
            }},
        })
        self.assertIs(response["result"]["isError"], True)
        self.assertIn("unsupported or missing fields", response["result"]["content"][0]["text"])
        self.assertFalse(self.calls.exists())

    def test_mcp_bridge_inactive_lease_never_calls_model(self) -> None:
        self.admission.deactivate("manual-A")
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_distill", "arguments": {
                "inputs": self._inputs(), "task_state": "verified_milestone",
            }},
        })
        self.assertIs(response["result"]["isError"], True)
        self.assertIn("not activated", response["result"]["content"][0]["text"])
        self.assertFalse(self.calls.exists())

    def test_runtime_without_c6_api_is_unavailable_before_model(self) -> None:
        with mock.patch.dict(sys.modules, {
            "mindie_knowledge.graded": None,
            "mindie_knowledge.graded.distill": None,
        }):
            with self.assertRaises(mindie_sidecar.SidecarError):
                self._call_sidecar()
        self.assertFalse(self.calls.exists())

    def test_mcp_runtime_without_c6_api_is_unavailable_before_model(self) -> None:
        with mock.patch.dict(sys.modules, {
            "mindie_knowledge.graded": None,
            "mindie_knowledge.graded.distill": None,
        }):
            response = knowledge_mcp.handle({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "knowledge_distill", "arguments": {
                    "inputs": self._inputs(), "task_state": "verified_milestone",
                }},
            })
        self.assertIs(response["result"]["isError"], True)
        self.assertIn("no C6 distillation support", response["result"]["content"][0]["text"])
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
