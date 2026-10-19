"""C6 organizer branch contract tests (skipped without the union runtime)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORGANIZER = ROOT / "scripts" / "dsh_organizer.py"
sys.path.insert(0, str(ROOT / "scripts"))
import dsh_organizer  # noqa: E402

try:
    import mindie_knowledge
    from mindie_knowledge.graded import distill
    from mindie_knowledge.materials import summarizer

    SHARED = Path(mindie_knowledge.__file__).resolve().parents[1]
except Exception:  # pragma: no cover - CI without the union runtime
    mindie_knowledge = distill = summarizer = None
    SHARED = None


class _Handler(BaseHTTPRequestHandler):
    token = "distill-token"
    text = ""
    usage = {"input_tokens": 11, "cached_input_tokens": 3, "output_tokens": 7}
    status = 200
    calls = 0
    last_body = None

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_body = json.loads(self.rfile.read(length))
        if self.headers.get("Authorization") != "Bearer " + self.token:
            self.send_response(403)
            self.end_headers()
            return
        if self.status != 200:
            body = json.dumps({"error": "model failed"}).encode()
            self.send_response(self.status)
        else:
            body = json.dumps({"text": self.text, "usage": self.usage}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class OrganizerDistillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if distill is None or summarizer is None:
            raise unittest.SkipTest("mindie-knowledge union runtime is unavailable")
        if os.name == "nt":  # pragma: no cover - the test scanner is a POSIX shim
            raise unittest.SkipTest("the fake Gitleaks shim is POSIX-only")

    def setUp(self) -> None:
        _Handler.calls = 0
        _Handler.last_body = None
        _Handler.status = 200
        _Handler.text = ""
        _Handler.usage = {"input_tokens": 11, "cached_input_tokens": 3, "output_tokens": 7}
        self._temporary = tempfile.TemporaryDirectory()
        self.state = Path(self._temporary.name)
        self.scanner = self.state / "fake-gitleaks"
        self.scanner.write_text("#!/bin/sh\ncat >/dev/null\nprintf '[]'\n", encoding="utf-8")
        self.scanner.chmod(0o755)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _env(self) -> dict:
        env = dict(os.environ, MINDIE_DSH_STATE_DIR=str(self.state))
        paths = [str(SHARED)]
        if existing := env.get("PYTHONPATH"):
            paths.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(paths)
        return env

    def _identity(self) -> dict:
        completed = subprocess.run(
            [sys.executable, str(ORGANIZER), "--identity"],
            env=self._env(), capture_output=True, text=True, timeout=60, check=True,
        )
        return json.loads(completed.stdout)

    def _request(self, *, task_state: str = "verified_milestone", outcome: str = "verified") -> dict:
        prepared = distill.prepare_request(
            [
                {
                    "kind": "experiment_ledger",
                    "outcome": outcome,
                    "source_locator": "experiment-ledger.jsonl",
                    "content": "two reproduced observations of the same rule",
                    "evidence_refs": ["run-1", "run-2"],
                    "repetitions": 2,
                }
            ],
            session_digest=("a" if task_state == "verified_milestone" else "b") * 64,
            task_state=task_state,
            redactor_executable=str(self.scanner),
            redaction_key=b"k" * 32,
        )
        return distill.with_worker_identity(prepared, self._identity())

    def _serve(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        endpoint = {
            "url": f"http://127.0.0.1:{server.server_address[1]}/summary",
            "token": _Handler.token,
            "provider": "deepseek-official",
            "model": "deepseek-flash",
        }
        (self.state / "summary-endpoint.json").write_text(json.dumps(endpoint), encoding="utf-8")

    def _run(self, payload: dict, *, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(ORGANIZER)],
            input=json.dumps(payload), env=self._env(), capture_output=True,
            text=True, timeout=60, check=check,
        )

    def _decision(self, request: dict) -> dict:
        item = request["sanitized_inputs"][0]
        frontmatter = {
            "schema": "mindie-graded-unit/1",
            "id": "unit-test-organizer-distill",
            "tier": "L2",
            "category": "sop",
            "evidence_strength": "single_observation",
            "domain_path": "profiling-and-diagnostics/anomaly-and-bubble",
            "inherits_from": [],
            "title": "Organizer C6 branch test unit",
            "triggers": {"error_signatures": ["E_TEST"], "keywords": ["organizer", "distill"]},
            "conditions": {"soc": ["Ascend910C"]},
            "provenance": {
                "source_type": "session_distill",
                "source_locator": item["source_locator"],
                "source_digest": item["source_digest"],
                "evidence_refs": ["run-1"],
                "supersedes": [],
            },
        }
        return {
            "schema_version": "mindie-distill-decision/1",
            "session_digest": request["session_digest"],
            "evidence_inputs": request["evidence_inputs"],
            "action": "propose_graded_units",
            "drop_reason": None,
            "units": [
                {
                    "target_tier": "L2",
                    "category": "sop",
                    "evidence_strength": "single_observation",
                    "domain_path": "profiling-and-diagnostics/anomaly-and-bubble",
                    "operation": "create",
                    "target_unit_id": "unit-test-organizer-distill",
                    "frontmatter": frontmatter,
                    "markdown_body": "Use the reproduced organizer branch.",
                }
            ],
        }

    def test_identity_declares_c6_capability(self) -> None:
        identity = self._identity()
        self.assertIn("mindie-distill-request/1", identity["capabilities"])
        summarizer.validate_identity(identity)

    def test_identity_does_not_claim_c6_without_runtime_api(self) -> None:
        with mock.patch.dict(os.environ, {"MINDIE_DSH_STATE_DIR": str(self.state)}), \
                mock.patch.dict(sys.modules, {
                    "mindie_knowledge.graded": None,
                    "mindie_knowledge.graded.distill": None,
                }):
            identity = dsh_organizer.identity()
        self.assertNotIn("mindie-distill-request/1", identity.get("capabilities", []))
        summarizer.validate_identity(identity)

    def test_native_protocol_returns_validated_decision(self) -> None:
        self._serve()
        request = self._request()
        _Handler.text = json.dumps(self._decision(request))
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "returned")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertEqual(envelope["billing_status"], "reported")
        self.assertTrue(envelope["usage_known"])
        self.assertEqual(envelope["result"]["action"], "propose_graded_units")
        self.assertEqual(_Handler.calls, 1)
        posted = _Handler.last_body
        self.assertEqual(posted["schema"], "mindie-distill-request/1")
        self.assertTrue(posted["prompt"])
        self.assertIsInstance(posted["output_schema"], dict)
        self.assertEqual(posted["request"]["sanitized_inputs"], request["sanitized_inputs"])

    def test_in_flight_is_noop_without_model_call(self) -> None:
        request = self._request(task_state="in_flight", outcome="in_flight")
        self._serve()
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "returned")
        self.assertEqual(envelope["result"]["action"], "noop")
        self.assertEqual(envelope["result"]["drop_reason"], "in_flight_incomplete")
        self.assertEqual(envelope["model_calls"], 0)
        self.assertEqual(envelope["billing_status"], "not_called")
        self.assertIsNone(envelope["usage"])
        self.assertFalse(envelope["usage_known"])
        self.assertEqual(_Handler.calls, 0)

    def test_missing_endpoint_fails_before_send(self) -> None:
        request = self._request()
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["error"], "configuration")
        self.assertEqual(envelope["error_reason"], "configuration_invalid")
        self.assertEqual(envelope["model_calls"], 0)
        self.assertEqual(envelope["billing_status"], "not_called")

    def test_after_send_failure_is_outcome_unknown(self) -> None:
        self._serve()
        request = self._request()
        _Handler.status = 500
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "outcome_unknown")
        self.assertEqual(envelope["error"], "unknown")
        self.assertEqual(envelope["error_reason"], "unknown_failure")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertEqual(envelope["billing_status"], "unknown")
        self.assertIsNone(envelope["usage"])
        self.assertFalse(envelope["usage_known"])
        self.assertEqual(_Handler.calls, 1)

    def test_missing_usage_is_unknown_not_zero(self) -> None:
        self._serve()
        request = self._request()
        _Handler.text = json.dumps(self._decision(request))
        _Handler.usage = {"output_tokens": 7}
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "returned")
        self.assertFalse(envelope["usage_known"])
        self.assertIsNone(envelope["usage"])
        self.assertEqual(envelope["billing_status"], "unknown")
        self.assertEqual(envelope["model_calls"], 1)

    def test_local_validation_does_not_reverse_remote_result(self) -> None:
        self._serve()
        request = self._request()
        decision = self._decision(request)
        decision["session_digest"] = "f" * 64
        _Handler.text = json.dumps(decision)
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["error"], "invalid_result")
        self.assertEqual(envelope["error_reason"], "result_contract_invalid")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertEqual(envelope["billing_status"], "reported")
        self.assertIsNotNone(envelope["usage"])
        self.assertIsNotNone(envelope["raw_result"])
        self.assertEqual(_Handler.calls, 1)

    def test_changed_model_route_is_rejected_before_paid_invocation(self) -> None:
        self._serve()
        request = self._request()
        endpoint_file = self.state / "summary-endpoint.json"
        changed = json.loads(endpoint_file.read_text())
        changed["model"] = "different-model"
        endpoint_file.write_text(json.dumps(changed))
        envelope = json.loads(self._run(request).stdout)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["error_reason"], "worker_policy_changed")
        self.assertEqual(envelope["model_calls"], 0)
        self.assertEqual(_Handler.calls, 0)

    def test_invalid_worker_request_fails_before_send(self) -> None:
        request = self._request()
        request["request_digest"] = "0" * 64
        completed = self._run(request, check=False)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(json.loads(completed.stdout)["error"], "invalid_distill_request")
        self.assertEqual(_Handler.calls, 0)

    def test_unknown_schema_is_rejected_explicitly(self) -> None:
        completed = self._run(
            {"schema_version": "mindie-distill-request/2", "sanitized_inputs": []},
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["schema"], "mindie-summary-error/1")
        self.assertEqual(payload["error"], "unsupported_request_schema")
        self.assertEqual(_Handler.calls, 0)


if __name__ == "__main__":
    unittest.main()
