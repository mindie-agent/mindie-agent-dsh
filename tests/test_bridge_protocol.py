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
    "knowledge_distill",
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
        by_name = {tool["name"]: tool for tool in tools}
        query = by_name["knowledge_query"]["inputSchema"]["properties"]
        self.assertTrue(
            {
                "domain", "domain_path", "token_budget", "include_unknown",
                "include_archived", "navigation_only", "run_id",
            }
            <= set(query)
        )
        self.assertEqual(query["include_archived"]["type"], "boolean")
        self.assertEqual(
            query["navigation_only"],
            {"type": "boolean", "description": "Return navigation cards without inline bodies."},
        )
        self.assertEqual(query["run_id"]["type"], "string")
        explain = by_name["knowledge_explain"]["inputSchema"]
        self.assertEqual(
            set(explain["properties"]),
            {"ref", "refs", "conditions", "known_refs", "token_budget", "run_id"},
        )
        self.assertEqual(explain["required"], [])
        self.assertEqual(
            explain["properties"]["refs"], {"type": "array", "items": {"type": "string"}}
        )
        self.assertEqual(
            explain["properties"]["known_refs"],
            {"type": "array", "items": {"type": "string"}},
        )
        self.assertEqual(
            explain["properties"]["token_budget"],
            {"type": "integer", "minimum": 1, "maximum": 16000},
        )
        self.assertEqual(explain["properties"]["run_id"]["type"], "string")
        distill = by_name["knowledge_distill"]["inputSchema"]
        self.assertEqual(
            set(distill["properties"]),
            {"domain", "inputs", "task_state", "related_refs", "proposal"},
        )
        self.assertEqual(set(distill["required"]), {"inputs", "task_state"})
        self.assertEqual(
            distill["properties"]["related_refs"],
            {"type": "array", "items": {"type": "string"}},
        )
        self.assertEqual(distill["properties"]["proposal"], {"type": "object"})
        item = distill["properties"]["inputs"]["items"]
        self.assertEqual(
            set(item["properties"]),
            {"kind", "outcome", "source_locator", "content", "evidence_refs", "repetitions"},
        )
        self.assertNotIn("_session_id", str(distill))
        self.assertNotIn("_activation", str(distill))
        self.assertNotIn("knowledge_feedback", {tool["name"] for tool in tools})

    def test_expanded_optional_arguments_pass_argument_validation(self) -> None:
        responses = exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_explain",
                        "arguments": {
                            "refs": [
                                "mindie://vllm-ascend/units/unit-a@1",
                                "mindie://vllm-ascend/units/unit-b@1",
                            ],
                            "conditions": {"soc": "Ascend910B"},
                            "known_refs": ["mindie://vllm-ascend/units/unit-a@1"],
                            "token_budget": 4096,
                            "run_id": "run-explain-fixture",
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_explain",
                        "arguments": {"conditions": {"soc": "Ascend910B"}},
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_distill",
                        "arguments": {
                            "inputs": [
                                {
                                    "kind": "authorized_transcript",
                                    "outcome": "verified",
                                    "source_locator": "fixture",
                                    "content": "fixture",
                                    "evidence_refs": [],
                                }
                            ],
                            "task_state": "verified_milestone",
                            "related_refs": ["unit-fixture-probe"],
                            "proposal": {"schema_version": "mindie-distill-decision/1"},
                        },
                    },
                },
                {
                    "jsonrpc": "2.0",
                    "id": 4,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_query",
                        "arguments": {
                            "query": "fixture",
                            "navigation_only": True,
                            "run_id": "run-query-fixture",
                        },
                    },
                },
            ],
            self._env(),
        )
        for response in responses:
            result = response["result"]
            self.assertIs(result["isError"], True)
            text = result["content"][0]["text"]
            self.assertNotIn("unexpected tool arguments", text)
            self.assertIn("Unavailable:", text)

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
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "knowledge_distill",
                        "arguments": {
                            "inputs": [
                                {
                                    "kind": "authorized_transcript",
                                    "outcome": "observed",
                                    "source_locator": "fixture",
                                    "content": "fixture",
                                    "evidence_refs": [],
                                }
                            ],
                            "task_state": "completed",
                            "_activation": "forged",
                        },
                    },
                },
            ],
            self._env(),
        )
        self.assertIs(responses[0]["result"]["isError"], True)
        self.assertIn("unknown tool", responses[0]["result"]["content"][0]["text"])
        self.assertIs(responses[1]["result"]["isError"], True)
        self.assertIn("unexpected tool arguments", responses[1]["result"]["content"][0]["text"])
        self.assertIs(responses[2]["result"]["isError"], True)
        self.assertIn("unexpected tool arguments", responses[2]["result"]["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
