"""Full DSH tool entry: host3 registration -> sidecar lease -> Service/Engine -> organizer -> native stub."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "tests" / "host_tool_harness.mjs"

try:
    import mindie_knowledge
    from mindie_knowledge import consent_store
    from mindie_knowledge.graded.distill import prepare_request
    from mindie_knowledge.loop import lifecycle
    from mindie_knowledge.loop import settings as settings_mod
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import connection_path
    from mindie_knowledge.loop.engine import Engine
    from mindie_knowledge.loop.lifecycle import config_fingerprint
    from mindie_knowledge.loop.store import Store, session_key
    from mindie_knowledge.loop.transport import Service

    SHARED = Path(mindie_knowledge.__file__).resolve().parents[1]
except Exception:  # pragma: no cover - CI without the union runtime
    mindie_knowledge = consent_store = prepare_request = None
    lifecycle = settings_mod = Admission = connection_path = Engine = None
    config_fingerprint = Store = session_key = Service = None
    SHARED = None

ORGANIZER = ROOT / "scripts" / "dsh_organizer.py"


class KnowledgeDistillEntryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if prepare_request is None or Service is None:
            raise unittest.SkipTest("mindie-knowledge union runtime is unavailable")
        if os.name == "nt":  # pragma: no cover - the fake Gitleaks shim is POSIX-only
            raise unittest.SkipTest("the fake Gitleaks shim is POSIX-only")
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is unavailable")

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self._old_env = dict(os.environ)
        self.addCleanup(self._restore_env)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.root / "state").mkdir()
        (self.root / "config").mkdir()
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
        self.scanner = self.root / "fake-gitleaks"
        self.scanner.write_text("#!/bin/sh\ncat >/dev/null\nprintf '[]'\n", encoding="utf-8")
        self.scanner.chmod(0o755)
        self.knowledge = self.root / "knowledge"
        self.store = Store(self.knowledge, "vllm-ascend")
        self.engine_config = self.root / "engine.json"
        self.config = {
            "root": str(self.knowledge),
            "domain": "vllm-ascend",
            "redactor_executable": str(self.scanner),
            "community_config": str(self.settings),
            "transcript_adapter": str(ROOT / "scripts" / "dsh_transcript.py"),
            "summary_command": [sys.executable, str(ORGANIZER)],
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
        (self.root / "venv").symlink_to(Path(sys.executable).parent.parent, target_is_directory=True)
        self.adapter_config = self.root / "config.json"
        self.adapter_config.write_text(json.dumps({
            "schema": "mindie-dsh-runtime/1", "version": "0.0.2",
            "python": sys.executable, "venv_dir": str(self.root / "venv"),
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
        })
        inputs = self._inputs()
        self.prepared = prepare_request(
            inputs, session_digest=session_key("manual-A"), task_state="verified_milestone",
            redactor_executable=str(self.scanner), redaction_key=self.store.redaction_key(),
        )
        self.decision = self._decision(self.prepared)
        self.inputs = inputs

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

    @staticmethod
    def _inputs() -> list[dict]:
        return [{
            "kind": "authorized_transcript",
            "outcome": "observed",
            "source_locator": "fixture@abcdef1:probe.txt",
            "content": "Fixture graph probe; no hardware claim.",
            "evidence_refs": ["fixture-observation"],
            "repetitions": 1,
        }]

    @staticmethod
    def _decision(prepared: dict) -> dict:
        source = prepared["sanitized_inputs"][0]
        frontmatter = {
            "schema": "mindie-graded-unit/1",
            "id": "unit-host-entry-distill",
            "tier": "L2",
            "category": "sop",
            "evidence_strength": "single_observation",
            "domain_path": "serving/graph",
            "inherits_from": [],
            "title": "Fixture DSH host entry probe",
            "triggers": {"error_signatures": [], "keywords": ["graph", "dsh"]},
            "conditions": {"soc": ["Ascend910B"]},
            "provenance": {
                "source_type": "session_distill",
                "source_locator": source["source_locator"],
                "source_digest": source["source_digest"],
                "evidence_refs": source["evidence_refs"],
                "supersedes": [],
            },
        }
        return {
            "schema_version": "mindie-distill-decision/1",
            "session_digest": prepared["session_digest"],
            "evidence_inputs": prepared["evidence_inputs"],
            "action": "propose_graded_units",
            "drop_reason": None,
            "units": [{
                "target_unit_id": frontmatter["id"],
                "target_tier": "L2",
                "category": "sop",
                "evidence_strength": "single_observation",
                "domain_path": frontmatter["domain_path"],
                "operation": "create",
                "frontmatter": frontmatter,
                "markdown_body": "Fixture DSH host entry probe; no hardware claim.\n",
            }],
        }

    def _run_harness(self, *, skip_activation: bool = False, model_mode: str = "returned") -> dict:
        env = dict(os.environ)
        paths = [str(SHARED)]
        if existing := env.get("PYTHONPATH"):
            paths.append(existing)
        env.update({
            "PYTHONPATH": os.pathsep.join(paths),
            "DISTILL_DECISION": json.dumps(self.decision),
            "DISTILL_INPUTS": json.dumps(self.inputs),
            "DISTILL_CWD": str(self.project),
            "DISTILL_MODEL_MODE": model_mode,
        })
        if skip_activation:
            env["DISTILL_SKIP_ACTIVATION"] = "1"
        completed = subprocess.run(
            [shutil.which("node"), str(HARNESS)], env=env,
            capture_output=True, text=True, timeout=90,
        )
        if completed.returncode != 0:
            self.fail(f"host tool harness failed: {completed.stderr or completed.stdout}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

    def test_registered_tool_uses_native_lease_service_and_organizer(self) -> None:
        from mindie_knowledge.loop.summary_budget import distill_rpc_timeout
        payload = self._run_harness()
        self.assertTrue(payload["activated"])
        self.assertIsNone(payload["error"])
        result = payload["result"]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["admitted_unit_ids"], ["unit-host-entry-distill"])
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(result["billing_status"], "reported")
        self.assertEqual(result["usage"]["output_tokens"], 7)
        self.assertEqual(payload["llmCalls"], 1)
        self.assertTrue(payload["promptHasDecisionSchema"])
        self.assertGreaterEqual(payload["toolTimeoutMs"], (distill_rpc_timeout() + 30) * 1000)
        found = self.store.query("graph", conditions={"soc": "Ascend910B"})
        self.assertIn("unit-host-entry-distill", [row["id"] for row in found["results"]])

    def test_unactivated_registered_tool_never_calls_model(self) -> None:
        payload = self._run_harness(skip_activation=True)
        self.assertFalse(payload["activated"])
        self.assertIn("not activated", payload["error"])
        self.assertIsNone(payload["result"])
        self.assertEqual(payload["llmCalls"], 0)

    def test_local_application_failure_preserves_the_native_decision(self) -> None:
        with mock.patch.object(self.store.graded, "install", side_effect=OSError("controlled local write failure")):
            payload = self._run_harness()
        self.assertIsNone(payload["error"])
        result = payload["result"]
        self.assertEqual(result["operation_status"], "local_application_failed")
        self.assertEqual(result["model_execution"], "completed")
        self.assertEqual(result["result"]["units"][0]["target_unit_id"], "unit-host-entry-distill")
        self.assertIn("controlled local write failure", result["local_error"]["message"])
        self.assertEqual(payload["llmCalls"], 1)

    def test_receipt_failure_preserves_decision_and_repeat_is_an_invoking_error(self) -> None:
        from mindie_knowledge.materials.summarizer import SummaryLedger
        with mock.patch.object(SummaryLedger, "record", side_effect=OSError("controlled receipt failure")):
            payload = self._run_harness()
        self.assertIsNone(payload["error"])
        result = payload["result"]
        self.assertEqual(result["operation_status"], "local_receipt_failed")
        self.assertEqual(result["model_execution"], "completed")
        self.assertEqual(result["result"], self.decision)
        self.assertEqual(payload["llmCalls"], 1)
        repeated = self._run_harness()
        self.assertIsNone(repeated["result"])
        self.assertEqual(json.loads(repeated["error"])["operation_status"], "invoking")
        self.assertEqual(repeated["llmCalls"], 0)

    def test_failed_model_result_is_a_native_tool_error(self) -> None:
        payload = self._run_harness(model_mode="invalid")
        self.assertIsNone(payload["result"])
        self.assertEqual(json.loads(payload["error"])["operation_status"], "failed")
        self.assertEqual(payload["llmCalls"], 1)

    def test_uncertain_model_result_is_a_native_tool_error(self) -> None:
        payload = self._run_harness(model_mode="error")
        self.assertIsNone(payload["result"])
        self.assertEqual(json.loads(payload["error"])["operation_status"], "outcome_unknown")
        self.assertEqual(payload["llmCalls"], 1)


if __name__ == "__main__":
    unittest.main()
