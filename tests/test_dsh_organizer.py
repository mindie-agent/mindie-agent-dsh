"""Organizer contract tests (skipped when the knowledge runtime is absent)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ORGANIZER = ROOT / "scripts" / "dsh_organizer.py"

try:
    from mindie_knowledge.materials import summarizer
except Exception:  # pragma: no cover - CI without the pinned runtime
    summarizer = None


class _Handler(BaseHTTPRequestHandler):
    token = "test-token"
    text = ""
    usage = {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 20}
    calls = 0

    def do_POST(self):
        type(self).calls += 1
        if self.headers.get("Authorization") != "Bearer " + self.token:
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        body = json.dumps(
            {
                "text": self.text,
                "usage": self.usage,
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class OrganizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if summarizer is None:
            raise unittest.SkipTest("mindie-knowledge runtime is unavailable")

    def _run_with_endpoint(self, usage, *, expired=False, organizer=ORGANIZER):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            block_id = "a" * 64
            result = {
                "blocks": [{"block_id": block_id, "title": "Title", "summary": "Summary"}],
                "navigation": {"title": "Task", "summary": "Navigation"},
            }
            _Handler.text = json.dumps(result)
            _Handler.usage = usage
            _Handler.calls = 0
            server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = {
                    "url": f"http://127.0.0.1:{server.server_address[1]}/summary",
                    "token": _Handler.token,
                    "provider": "deepseek-official",
                    "model": "deepseek-flash",
                }
                (state / "summary-endpoint.json").write_text(json.dumps(endpoint))
                env = dict(os.environ, MINDIE_DSH_STATE_DIR=str(state))
                if expired:
                    env["MINDIE_SUMMARY_WORKER_DEADLINE"] = str(time.monotonic() - 1)
                identity_run = subprocess.run(
                    [sys.executable, str(organizer), "--identity"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                identity = json.loads(identity_run.stdout)
                request = summarizer.make_request(
                    task_id="b" * 64,
                    body_version="rev-1",
                    blocks=[
                        {
                            "block_id": block_id,
                            "text": "public material",
                            "source_range": {"start": 0, "end": 15},
                        }
                    ],
                    prior_navigation=None,
                    identity=identity,
                )
                completed = subprocess.run(
                    [sys.executable, str(organizer)],
                    input=json.dumps(request),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                )
                envelope = json.loads(completed.stdout)
                summarizer.validate_outcome(envelope, request)
                return envelope, _Handler.calls
            finally:
                server.shutdown()
                server.server_close()

    def test_returned_outcome_validates(self) -> None:
        envelope, calls = self._run_with_endpoint(
            {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 20}
        )
        self.assertEqual(calls, 1)
        self.assertEqual(envelope["status"], "returned")
        self.assertEqual(envelope["billing_status"], "reported")
        self.assertEqual(envelope["usage"]["output_tokens"], 20)

    def test_installed_worker_survives_removing_setup_checkout(self) -> None:
        spec = importlib.util.spec_from_file_location("dsh_setup_persistence", ROOT / "scripts" / "setup.py")
        setup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / "checkout"
            shutil.copytree(ROOT / "scripts", source / "scripts")
            with mock.patch.object(setup.mp, "PACKAGE_DIR", source), mock.patch.dict(
                os.environ,
                MINDIE_DSH_DATA_HOME=str(directory / "data"),
                MINDIE_DSH_CONFIG_HOME=str(directory / "config"),
            ):
                config = setup.engine_configuration(str(directory / "unused-redactor"))
            shutil.rmtree(source)
            envelope, calls = self._run_with_endpoint(
                {"input_tokens": 10, "cached_input_tokens": 2, "output_tokens": 20},
                organizer=Path(config["summary_command"][1]),
            )
            self.assertEqual(calls, 1)
            self.assertEqual(envelope["status"], "returned")
            self.assertEqual(envelope["result"]["navigation"]["summary"], "Navigation")

    def test_missing_usage_does_not_become_zero_cost(self) -> None:
        envelope, calls = self._run_with_endpoint({"output_tokens": 20})
        self.assertEqual(calls, 1)
        self.assertEqual(envelope["status"], "returned")
        self.assertFalse(envelope["usage_known"])
        self.assertIsNone(envelope["usage"])
        self.assertEqual(envelope["billing_status"], "unknown")

    def test_expired_budget_never_posts_a_model_request(self) -> None:
        envelope, calls = self._run_with_endpoint({}, expired=True)
        self.assertEqual(calls, 0)
        self.assertEqual(envelope["status"], "failed")
        self.assertEqual(envelope["error"], "deadline")
        self.assertEqual(envelope["model_calls"], 0)
        self.assertEqual(envelope["billing_status"], "not_called")

    def test_missing_endpoint_is_a_configuration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            env = dict(os.environ, MINDIE_DSH_STATE_DIR=str(state))
            identity = json.loads(
                subprocess.run(
                    [sys.executable, str(ORGANIZER), "--identity"],
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                ).stdout
            )
            request = summarizer.make_request(
                task_id="c" * 64,
                body_version="rev-1",
                blocks=[
                    {
                        "block_id": "d" * 64,
                        "text": "public material",
                        "source_range": {"start": 0, "end": 15},
                    }
                ],
                prior_navigation=None,
                identity=identity,
            )
            completed = subprocess.run(
                [sys.executable, str(ORGANIZER)],
                input=json.dumps(request),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            )
            envelope = json.loads(completed.stdout)
            summarizer.validate_outcome(envelope, request)
            self.assertEqual(envelope["status"], "failed")
            self.assertEqual(envelope["error"], "configuration")
            self.assertEqual(envelope["billing_status"], "not_called")


if __name__ == "__main__":
    unittest.main()
