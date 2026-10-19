"""MCP protocol tests that never require the pinned runtime."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "knowledge_mcp.py"

EXPECTED_TOOLS = {
    "knowledge_query",
    "knowledge_explain",
    "knowledge_sync",
}


def exchange(messages: list[dict], env: dict[str, str]) -> list[dict]:
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "knowledge"],
        input=payload,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"bridge exited {completed.returncode}: {completed.stderr}"
        )
    rows = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


class BridgeProtocolTests(unittest.TestCase):
    def _env(self) -> dict[str, str]:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env = dict(os.environ)
        env.update(
            {
                "MINDIE_DSH_CONFIG": str(root / "missing-config.json"),
                "MINDIE_DSH_ENGINE_CONFIG": str(root / "missing-engine.json"),
                "MINDIE_DSH_STATE_DIR": str(root / "state"),
                "MINDIE_DSH_NO_REEXEC": "1",
            }
        )
        return env

    def test_initialize_and_tool_catalog(self) -> None:
        responses = exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2026-07-28"},
                },
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ],
            self._env(),
        )
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0]["result"]["protocolVersion"], "2026-07-28")
        tools = responses[1]["result"]["tools"]
        self.assertEqual({tool["name"] for tool in tools}, EXPECTED_TOOLS)
        for tool in tools:
            self.assertEqual(tool["inputSchema"]["type"], "object")
            self.assertIn("additionalProperties", tool["inputSchema"])
        self.assertNotIn("knowledge_feedback", {tool["name"] for tool in tools})

    def test_missing_runtime_returns_actionable_error(self) -> None:
        responses = exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {"query": "ascend"},
                    },
                }
            ],
            self._env(),
        )
        result = responses[0]["result"]
        self.assertIs(result["isError"], True)
        text = result["content"][0]["text"]
        self.assertIn("Unavailable:", text)
        self.assertIn("setup.py", text)
        self.assertIn("--python 3.11", text)

    def test_invalid_tool_calls_return_errors(self) -> None:
        responses = exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "knowledge_feedback", "arguments": {}},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {"query": "x", "session_id": "forged"},
                    },
                },
            ],
            self._env(),
        )
        self.assertIs(responses[0]["result"]["isError"], True)
        self.assertIn("unknown tool", responses[0]["result"]["content"][0]["text"])
        self.assertIs(responses[1]["result"]["isError"], True)
        self.assertIn("unexpected tool arguments", responses[1]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
