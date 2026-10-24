"""Production-path C6 routing test: organizer -> host3 handler -> stub model -> shared validation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORGANIZER = ROOT / "scripts" / "dsh_organizer.py"
HARNESS = ROOT / "tests" / "host_summary_harness.mjs"

try:
    import mindie_knowledge
    from mindie_knowledge.graded import distill
    from mindie_knowledge.materials import summarizer

    SHARED = Path(mindie_knowledge.__file__).resolve().parents[1]
except Exception:  # pragma: no cover - CI without the union runtime
    mindie_knowledge = distill = summarizer = None
    SHARED = None


class HostDistillRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if distill is None or summarizer is None:
            raise unittest.SkipTest("mindie-knowledge union runtime is unavailable")
        if os.name == "nt":  # pragma: no cover - the fake Gitleaks shim is POSIX-only
            raise unittest.SkipTest("the fake Gitleaks shim is POSIX-only")
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is unavailable")

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.state = Path(self._temporary.name)
        self.scanner = self.state / "fake-gitleaks"
        self.scanner.write_text("#!/bin/sh\ncat >/dev/null\nprintf '[]'\n", encoding="utf-8")
        self.scanner.chmod(0o755)
        self._processes: list[subprocess.Popen] = []

    def tearDown(self) -> None:
        for process in self._processes:
            self._stop(process)
        self._temporary.cleanup()

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

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
        return prepared

    def _decision(self, request: dict) -> dict:
        item = request["sanitized_inputs"][0]
        frontmatter = {
            "schema": "mindie-graded-unit/1",
            "id": "unit-test-host-distill",
            "tier": "L2",
            "category": "sop",
            "evidence_strength": "single_observation",
            "domain_path": "profiling-and-diagnostics/anomaly-and-bubble",
            "inherits_from": [],
            "title": "Host C6 route test unit",
            "triggers": {"error_signatures": ["E_HOST"], "keywords": ["host", "distill"]},
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
                    "target_unit_id": "unit-test-host-distill",
                    "frontmatter": frontmatter,
                    "markdown_body": "Use the reproduced host route.",
                }
            ],
        }

    def _start_harness(self, *, text: str, usage: dict | None = None,
                       mode: str = "ok") -> tuple[dict, Path]:
        directory = Path(tempfile.mkdtemp(dir=self.state, prefix="harness-"))
        endpoint_path = directory / "endpoint.json"
        capture_path = directory / "capture.json"
        env = dict(self._env())
        env.update({
            "DISTILL_STUB_ENDPOINT": str(endpoint_path),
            "DISTILL_STUB_CAPTURE": str(capture_path),
            "DISTILL_STUB_TEXT": text,
            "DISTILL_STUB_MODE": mode,
        })
        if usage is not None:
            env["DISTILL_STUB_USAGE"] = json.dumps(usage)
        process = subprocess.Popen(
            [shutil.which("node"), str(HARNESS)], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        self._processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if endpoint_path.exists():
                try:
                    return json.loads(endpoint_path.read_text(encoding="utf-8")), capture_path
                except ValueError:
                    pass
            if process.poll() is not None:
                break
            time.sleep(0.05)
        process.terminate()
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, stderr = process.communicate()
        self.fail(f"host summary harness did not start: {stderr or stdout}")

    def _write_endpoint(self, endpoint: dict) -> None:
        value = {**endpoint, "provider": "stub-provider", "model": "stub-model"}
        (self.state / "summary-endpoint.json").write_text(json.dumps(value), encoding="utf-8")

    def _run_organizer(self, request: dict) -> dict:
        completed = subprocess.run(
            [sys.executable, str(ORGANIZER)],
            input=json.dumps(request), env=self._env(), capture_output=True,
            text=True, timeout=60, check=True,
        )
        return json.loads(completed.stdout)

    @staticmethod
    def _post(endpoint: dict, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            endpoint["url"],
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + endpoint["token"],
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    @staticmethod
    def _prompt(capture: Path) -> str:
        value = json.loads(capture.read_text(encoding="utf-8"))
        return value["messages"][0]["content"][0]["text"]

    def test_organizer_payload_reaches_actual_handler_and_shared_validation(self) -> None:
        request = self._request()
        decision = self._decision(request)
        endpoint, capture = self._start_harness(
            text=json.dumps(decision),
            usage={"inputTokens": 11, "cacheReadTokens": 3, "outputTokens": 7},
        )
        self._write_endpoint(endpoint)
        request = distill.with_worker_identity(request, self._identity())
        envelope = self._run_organizer(request)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "returned")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertEqual(envelope["billing_status"], "reported")
        self.assertEqual(envelope["usage"]["output_tokens"], 7)
        captured = json.loads(capture.read_text(encoding="utf-8"))
        prompt = self._prompt(capture)
        self.assertNotIn('Use the key name "blocks"', prompt)
        self.assertIn("mindie-distill-decision/1", prompt)
        for unsupported in ("schema", "responseFormat", "outputSchema", "response_format"):
            self.assertNotIn(unsupported, captured["optionKeys"])

    def test_in_flight_never_reaches_the_handler(self) -> None:
        request = self._request(task_state="in_flight", outcome="in_flight")
        endpoint, capture = self._start_harness(text="must not be used")
        self._write_endpoint(endpoint)
        request = distill.with_worker_identity(request, self._identity())
        envelope = self._run_organizer(request)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["result"]["action"], "noop")
        self.assertEqual(envelope["result"]["drop_reason"], "in_flight_incomplete")
        self.assertEqual(envelope["model_calls"], 0)
        self.assertEqual(envelope["billing_status"], "not_called")
        self.assertFalse(capture.exists())

    def test_returned_without_usage_stays_unknown(self) -> None:
        request = self._request()
        endpoint, _capture = self._start_harness(
            text=json.dumps(self._decision(request)), mode="no-usage",
        )
        self._write_endpoint(endpoint)
        request = distill.with_worker_identity(request, self._identity())
        envelope = self._run_organizer(request)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "returned")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertFalse(envelope["usage_known"])
        self.assertIsNone(envelope["usage"])
        self.assertEqual(envelope["billing_status"], "unknown")

    def test_model_failure_after_send_is_outcome_unknown(self) -> None:
        request = self._request()
        endpoint, capture = self._start_harness(text="", mode="error")
        self._write_endpoint(endpoint)
        request = distill.with_worker_identity(request, self._identity())
        envelope = self._run_organizer(request)
        summarizer.validate_outcome(envelope, request)
        self.assertEqual(envelope["status"], "outcome_unknown")
        self.assertEqual(envelope["model_calls"], 1)
        self.assertEqual(envelope["billing_status"], "unknown")
        self.assertTrue(capture.exists())

    def test_legacy_payload_keeps_the_summary_shape(self) -> None:
        endpoint, capture = self._start_harness(
            text='{"blocks":[],"navigation":{"title":"T","summary":"S"}}',
        )
        status, _body = self._post(endpoint, {
            "request": {"task_id": "a" * 64},
            "prompt": "index these blocks",
        })
        self.assertEqual(status, 200)
        prompt = self._prompt(capture)
        self.assertIn('Use the key name "blocks"', prompt)
        self.assertNotIn("mindie-distill-decision/1", prompt)

    def test_unknown_payload_schema_is_rejected(self) -> None:
        endpoint, _capture = self._start_harness(text="")
        status, body = self._post(endpoint, {
            "schema": "mindie-distill-request/2",
            "prompt": "p",
            "request": {},
        })
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "unsupported summary payload schema")

    def test_distill_payload_requires_shared_schema(self) -> None:
        endpoint, _capture = self._start_harness(text="")
        status, body = self._post(endpoint, {
            "schema": "mindie-distill-request/1",
            "prompt": "p",
            "request": {},
        })
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid distillation payload")

    def test_mcp_explain_forwards_batch_closure_arguments(self) -> None:
        sys.path.insert(0, str(ROOT / "scripts"))
        import knowledge_mcp
        from mindie_knowledge.loop.activation import Admission
        from mindie_knowledge.loop import transport

        Admission(self.state / "admission.sqlite3").activate(
            "native-fixture", project_root=str(self.state))
        arguments = {
            "refs": ["mindie://vllm-ascend/units/unit-a@1"],
            "conditions": {"soc": "Ascend910B"},
            "known_refs": ["unit-b@1"],
            "token_budget": 2048,
        }
        captured = {}

        def fake_rpc(connection, method, payload, timeout=None):
            captured.update({
                "connection": connection,
                "method": method,
                "payload": payload,
                "timeout": timeout,
            })
            return {"units": [], "usage": {}}

        route = mock.Mock()
        route.selected_path = str(self.state / "knowledge")
        route.cross_domain = False
        route.forward_domain = None
        connection = object()
        config = {"engine_config": str(self.state / "engine.json")}
        with mock.patch.dict(os.environ, MINDIE_DSH_SESSION_ID="native-fixture",
                             MINDIE_DSH_DATA_HOME=str(self.state)), \
                mock.patch.object(knowledge_mcp, "_route", return_value=route), \
                mock.patch.object(
                    knowledge_mcp, "knowledge_connection", return_value=connection,
                ), \
                mock.patch.object(transport, "rpc", side_effect=fake_rpc):
            result = knowledge_mcp.call_knowledge("knowledge_explain", arguments, config)

        self.assertEqual(result, {"units": [], "usage": {}})
        self.assertEqual(captured["method"], "explain")
        self.assertEqual(captured["connection"], connection)
        self.assertEqual(captured["timeout"], 30.0)
        payload = captured["payload"]
        self.assertEqual(payload["refs"], arguments["refs"])
        self.assertEqual(payload["conditions"], arguments["conditions"])
        self.assertEqual(payload["known_refs"], arguments["known_refs"])
        self.assertEqual(payload["token_budget"], arguments["token_budget"])


if __name__ == "__main__":
    unittest.main()
