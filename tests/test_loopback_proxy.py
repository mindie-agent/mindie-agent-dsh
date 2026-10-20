import importlib.util
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


class LoopbackProxyTests(unittest.TestCase):
    def test_summary_is_sent_once_directly_when_a_proxy_is_configured(self):
        spec = importlib.util.spec_from_file_location(
            "loopback_organizer",
            Path(__file__).resolve().parents[1] / "scripts/dsh_organizer.py",
        )
        organizer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(organizer)
        calls = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.headers["Authorization"])
                self.rfile.read(int(self.headers["Content-Length"]))
                body = b'{"text":"summary","usage":{"output_tokens":3}}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(
                target=lambda: server.serve_forever(poll_interval=0.01), daemon=True
            )
            thread.start()
            env = {
                key: value for key, value in os.environ.items()
                if key.lower() not in {"http_proxy", "https_proxy", "all_proxy", "no_proxy"}
            }
            env.update(HTTP_PROXY="http://127.0.0.1:1", NO_PROXY="")
            try:
                with patch.dict(os.environ, env, clear=True):
                    result = organizer._post({
                        "url": f"http://127.0.0.1:{server.server_port}/summary",
                        "token": "local-token",
                    })
                self.assertEqual(result, ("summary", {"output_tokens": 3}))
                self.assertEqual(calls, ["Bearer local-token"])
            finally:
                server.shutdown()
                thread.join(timeout=2)
