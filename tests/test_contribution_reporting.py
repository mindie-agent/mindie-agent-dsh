"""Actual DSH producers share one profile authority and reporter queue."""
import json
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import capture_stop
import dsh_diagnostics
import mindie_sidecar
from mindie_knowledge import consent_store
from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.outbox import Outbox


class ContributionReportingTests(unittest.TestCase):
    def test_real_rejected_stop_is_recorded_but_disabled_capture_is_not_a_fault(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            logs = root / "logs"
            engine = root / "engine.json"
            engine.write_text(json.dumps({
                "root": str(root / "data"), "domain": "vllm-ascend",
                "community_config": str(root / "mindie-community.json"),
                "transcript_adapter": str(Path(mindie_sidecar.__file__).with_name("dsh_transcript.py")),
                # Rejection occurs before scanning; no redactor process is invoked.
                "redactor_executable": sys.executable,
            }))
            with patch.dict(os.environ, MINDIE_DSH_CONFIG_HOME=str(root),
                            MINDIE_DSH_STATE_DIR=str(root / "state"), MINDIE_DIAGNOSTICS_ROOT=str(logs)):
                consent_store.record_choice(root / "mindie-consent.json", "contribute")
                mindie_sidecar._configure_community({
                    "enabled": True, "repository": "account/knowledge", "account": "account",
                    "project_roots": [str(root)], "visibility": "public",
                    "consent_config": str(root / "mindie-consent.json"),
                })
                dsh_diagnostics.activate(ensure=False)
                # The real shared handoff rejects a native Stop without turn identity.
                envelope = json.dumps({"hook_event_name": "Stop", "session_id": "session"}).encode()
                for choice in ("contribute", "disabled"):
                    consent_store.record_choice(root / "mindie-consent.json", choice)
                    with patch.object(sys, "stdin", io.TextIOWrapper(io.BytesIO(envelope))):
                        self.assertEqual(capture_stop.main(), 0)
                rows = [json.loads(line) for line in capture_stop.log_path().read_text().splitlines()]
                self.assertEqual(rows[0]["receipt"]["stage"], "rejected")
                self.assertIn("incident_id", rows[0]["diagnostic"])
                self.assertEqual(rows[1]["receipt"]["stage"], "inert")
                self.assertNotIn("diagnostic", rows[1])
                from mindie_diagnostics import logging
                for (_component, location), recorder in list(logging._FAILURE_RECORDERS.items()):
                    if Path(location) == logs:
                        recorder.close()

    def test_explicit_choice_activates_worker_and_real_faults_reach_its_queue(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            config = root / "profile"
            logs = root / "logs"
            config.mkdir()
            engine = config / "engine.json"
            engine.write_text(json.dumps({"community_config": str(config / "mindie-community.json")}))
            (config / "config.json").write_text(json.dumps({
                "python": sys.executable, "engine_config": str(engine),
                "state_dir": str(root / "state"), "venv_dir": str(root / "venv"),
            }))
            with patch.dict(os.environ, MINDIE_DSH_CONFIG_HOME=str(config),
                            MINDIE_DIAGNOSTICS_ROOT=str(logs),
                            MINDIE_DIAGNOSTICS_CONFIG=str(root / "wrong-profile.json")), \
                    patch.object(mindie_sidecar, "_ensure_fork", return_value="account/knowledge"), \
                    patch("mindie_diagnostics.reporting_commands.ensure",
                          return_value={"status": "degraded", "category": "test_service_unavailable"}) as ensure:
                authority = config / "mindie-consent.json"
                authority.write_text(json.dumps({
                    "schema": "mindie-consent/1", "choice": "contribute",
                    "reporting": "disabled",
                }))
                before = authority.read_bytes()
                self.assertEqual(dsh_diagnostics.activate()["status"], "needs_choice")
                self.assertEqual(authority.read_bytes(), before)
                ensure.assert_not_called()
                result = mindie_sidecar.op_configure({
                    "choice": "contribute", "cwd": str(root), "account": "account",
                })
                self.assertEqual(result["sharing"]["reporting_activation"]["status"], "degraded")
                ensure.assert_called_once_with(config / "diagnostics.json", unit_dir=None)
                revision = consent_store.read(authority)["choice_revision"]
                self.assertTrue(revision)
                self.assertEqual(dsh_diagnostics.activate(ensure=False)["status"], "configured")
                self.assertEqual(consent_store.read(authority)["choice_revision"], revision)
                first = mindie_sidecar.diagnostic("rpc", RuntimeError("synthetic public fixture"))
                second = capture_stop.diagnose("hook", RuntimeError("synthetic public fixture"))
                queue = Outbox(root / "queue.sqlite3")
                self.assertEqual(ingest(logs, queue, config=config / "diagnostics.json")["enqueued"], 2)
                self.assertEqual(
                    {incident for row in queue.rows() for incident in row["incident_ids"]},
                    {first["incident_id"], second["incident_id"]},
                )
                mindie_sidecar.op_configure({"choice": "disabled", "cwd": str(root)})
                self.assertEqual(queue.withdraw_unconsented(), 2)
                self.assertEqual(consent_store.read(config / "mindie-consent.json")["choice"], "disabled")
                self.assertEqual(ensure.call_count, 1)
                from mindie_diagnostics import logging
                for (_component, location), recorder in list(logging._FAILURE_RECORDERS.items()):
                    if Path(location) == logs:
                        recorder.close()

    def test_startup_preserves_every_legacy_choice_without_activating_worker(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            with patch.dict(os.environ, MINDIE_DSH_CONFIG_HOME=str(root),
                            MINDIE_DIAGNOSTICS_ROOT=str(root / "logs")), \
                    patch("mindie_diagnostics.reporting_commands.ensure") as ensure:
                for choice in ("contribute", "disabled", "later", "read-only"):
                    authority = root / "mindie-consent.json"
                    authority.write_text(json.dumps({
                        "schema": "mindie-consent/1", "choice": choice, "reporting": "disabled",
                    }))
                    before = authority.read_bytes()
                    result = dsh_diagnostics.activate()
                    self.assertEqual(result["status"], "needs_choice")
                    self.assertFalse(result["enabled"])
                    self.assertEqual(authority.read_bytes(), before)
                ensure.assert_not_called()
