"""The shared runtime owns service recovery and its connection record."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mindie_sidecar
from mindie_knowledge.loop import cli


class ConnectionTests(unittest.TestCase):
    def test_service_failure_preserves_endpoint_and_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            engine = root / "engine.json"
            engine.write_text(json.dumps({
                "root": str(root / "data"), "domain": "vllm-ascend",
                "transcript_adapter": str(Path(mindie_sidecar.__file__).with_name("dsh_transcript.py")),
                "redactor_executable": sys.executable,
            }))
            config = root / "config.json"
            config.write_text(json.dumps({
                "python": sys.executable, "engine_config": str(engine),
                "state_dir": str(root / "state"), "venv_dir": str(root / "venv"),
            }))
            endpoint = cli.connection_path(cli.config_at(str(engine)))
            endpoint.parent.mkdir(parents=True)
            original = b'{"pid":123,"token":"existing-owner"}\n'
            for error in (RuntimeError("service state is unknown"),
                          RuntimeError("worker failed during startup"),
                          PermissionError("cannot inspect service")):
                with self.subTest(error=str(error)):
                    endpoint.write_bytes(original)
                    with patch.dict(os.environ, MINDIE_DSH_CONFIG=str(config),
                                    MINDIE_DSH_CONFIG_HOME=str(root),
                                    MINDIE_DIAGNOSTICS_ROOT=str(root / "logs")), \
                            patch.object(cli, "ensure_service",
                                         side_effect=[error, {"unexpected": "new service"}]) as ensure:
                        with self.assertRaises(type(error)) as raised:
                            mindie_sidecar.connection()
                    self.assertIs(raised.exception, error)
                    self.assertEqual(endpoint.read_bytes(), original)
                    ensure.assert_called_once_with(str(engine))

    def test_ready_connection_is_returned_unchanged(self):
        # Service liveness belongs to ensure_service; the adapter must not reinterpret it.
        ready = {"host": "127.0.0.1", "port": 12345, "token": "ready-owner"}
        with patch.object(mindie_sidecar, "engine_path", return_value="engine.json"), \
                patch.object(mindie_sidecar.diagnostics, "bind_environment"), \
                patch.object(cli, "ensure_service", return_value=ready):
            self.assertIs(mindie_sidecar.connection(), ready)

    def test_corrupt_engine_config_and_unreadable_admission_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            engine = root / "engine.json"
            engine.write_text("{corrupt", encoding="utf-8")
            config = root / "config.json"
            config.write_text(json.dumps({
                "python": sys.executable, "engine_config": str(engine),
                "state_dir": str(root / "state"), "venv_dir": str(root / "venv"),
            }))
            (root / "admission.sqlite3").write_bytes(b"corrupt-sqlite")
            with patch.dict(os.environ, MINDIE_DSH_CONFIG=str(config),
                            MINDIE_DSH_CONFIG_HOME=str(root),
                            MINDIE_DSH_DATA_HOME=str(root)):
                with self.assertRaises(mindie_sidecar.SidecarError) as bad_engine:
                    mindie_sidecar.public_settings()
                self.assertIn("cannot read engine configuration", str(bad_engine.exception))
                with self.assertRaises(mindie_sidecar.SidecarError) as bad_admission:
                    mindie_sidecar.op_rpc({
                        "session": "s1", "token": "t1", "method": "query",
                        "arguments": {"query": "x", "domain": "vllm-ascend"},
                    })
                self.assertIn("admission check failed", str(bad_admission.exception))
