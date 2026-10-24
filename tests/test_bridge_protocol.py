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


def real_explore_result(method="query"):
    """One real `mode="explore"` payload from the installed knowledge engine.

    Explore stripping, foundation promotion and the subagent fact pack are the
    engine's contract; adapter tests assert against its actual output rather
    than a hand-written response that could drift from the real schema.
    """
    from mindie_knowledge.graded.index import RoutingIndex
    from mindie_knowledge.graded.units import GradedUnit

    observed = {"soc": "Ascend910B"}

    def graded_unit(ident, tier, category, body, parents=(), strength="single_observation"):
        return GradedUnit.from_parts(
            {
                "schema": "mindie-graded-unit/1", "id": ident, "tier": tier,
                "category": category, "evidence_strength": strength,
                "domain_path": "serving/graph", "inherits_from": list(parents),
                "title": f"{ident} probe",
                "triggers": {"error_signatures": [], "keywords": ["rmsnorm"]},
                "conditions": {"soc": ["Ascend910B"]},
                "provenance": {"source_type": "session_distill",
                               "source_locator": "fixture@abc:probe.txt",
                               "source_digest": "0" * 64,
                               "evidence_refs": ["fixture"], "supersedes": []},
            },
            body,
            f"serving/graph/{ident}.md",
        )

    units = [
        graded_unit("unit-l1-foundation", "L1", "axiom", "Keep 32B UB alignment.",
                    strength="hardware_spec"),
        graded_unit("unit-l3-workflow", "L3", "workflow", "Old rmsnorm workflow steps.",
                    parents=["unit-l1-foundation"]),
        graded_unit("unit-l4-mutator", "L4", "mutator", "Fuse the rmsnorm epilogue."),
    ]
    index = RoutingIndex(units)
    options = dict(
        conditions=observed, mode="explore", token_budget=16000,
        native_subcontext=True, goal="Fuse RMSNorm epilogue",
        ledger_entries=[{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64",
                         "conditions": observed, "axes": {"evidence_validity": "valid"}}],
    )
    if method == "explain":
        return index.explain_closure(
            ["mindie://mindie/units/unit-l3-workflow", "mindie://mindie/units/unit-l4-mutator"],
            domain="mindie", **options)
    return index.query("rmsnorm", **options)


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
            {
                "ref", "refs", "conditions", "known_refs", "token_budget", "run_id",
                "mode", "ledger_entries", "bottleneck_summary", "native_subcontext", "goal",
            },
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

    def test_rsi_explore_query_and_explain_deliver_the_engine_result_verbatim(self) -> None:
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        import knowledge_mcp
        import mindie_sidecar
        import rsi_explore
        from unittest import mock

        # Argument shaping is this lane's only RSI responsibility: reject an
        # unknown mode before any service/store call, and forward the selectors.
        with self.assertRaises(ValueError):
            rsi_explore.forwarded({"query": "rmsnorm", "mode": "invalid"})
        query_raw = {
            "query": "rmsnorm", "mode": "explore", "native_subcontext": True,
            "goal": "Fuse RMSNorm epilogue",
            "ledger_entries": [{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64"}],
        }
        shaped = rsi_explore.forwarded(query_raw)
        self.assertEqual(shaped["mode"], "explore")
        self.assertIs(shaped["native_subcontext"], True)

        # Explore stripping, foundation promotion and the subagent fact pack are
        # the knowledge engine's contract. Both DSH adapters must deliver that
        # engine result verbatim; only the RPC transport boundary is mocked, and
        # it returns the genuine RoutingIndex output.
        session = "manual-dsh"
        explain_raw = {
            "refs": ["mindie://mindie/units/unit-l3-workflow", "mindie://mindie/units/unit-l4-mutator"], "mode": "explore", "native_subcontext": True,
            "goal": "Fuse RMSNorm epilogue", "conditions": {"soc": "Ascend910B"},
            "ledger_entries": [{"run_id": "r1", "verdict": "refuted", "hypothesis": "tile=64"}],
        }

        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        root = Path(workspace.name)
        engine_config = root / "engine.json"
        engine_config.write_text(json.dumps({
            "domain": "mindie", "root": str(root / "domain"),
            "admission_path": str(root / "admission.sqlite3"),
        }), encoding="utf-8")

        def assert_verbatim(delivered):
            self.assertEqual(delivered, engine_result)
            self.assertEqual(delivered["stripped_l3_ids"], ["unit-l3-workflow"])
            self.assertNotIn(
                "unit-l3-workflow", [row["id"] for row in (delivered.get("results", []) + delivered.get("units", []) + delivered.get("requires", []))])
            context = delivered["exploration_context"]
            pack = context["subagent_fact_pack"]
            self.assertEqual(pack["schema_version"], "mindie-rsi-subagent-pack/1")
            self.assertEqual(pack["isolation_mode"], "native_subcontext")
            # conditional_avoid (with its `avoid` field) and the usage estimate
            # arrive exactly as the engine produced them.
            self.assertIn("avoid", context["conditional_avoid"][0])
            self.assertEqual(
                context["conditional_avoid"],
                engine_result["exploration_context"]["conditional_avoid"])
            self.assertEqual(delivered["usage"], engine_result["usage"])

        # (1) knowledge_mcp.call_knowledge routes from a real local engine config;
        # the knowledge service is reached only through the mocked transport.
        data_home = root / "data"
        data_home.mkdir()
        with mock.patch.dict(os.environ, {
                "MINDIE_DSH_SESSION_ID": session,
                "MINDIE_DSH_DATA_HOME": str(data_home)}):
            from mindie_knowledge.loop.activation import Admission
            Admission(data_home / "admission.sqlite3").activate(session, project_root=str(root))
            config = {"engine_config": str(engine_config)}
            for name, raw, method in (
                    ("knowledge_query", query_raw, "query"),
                    ("knowledge_explain", explain_raw, "explain")):
                engine_result = real_explore_result(method)
                served = json.loads(json.dumps(engine_result))
                with self.subTest(adapter="call_knowledge", method=method), \
                        mock.patch.object(
                            knowledge_mcp, "knowledge_connection", return_value=object()), \
                        mock.patch(
                            "mindie_knowledge.loop.transport.rpc", return_value=served) as rpc:
                    delivered = knowledge_mcp.call_knowledge(name, dict(raw), config)
                self.assertEqual(rpc.call_args.args[1], method)
                self.assertEqual(rpc.call_args.args[2]["mode"], "explore")
                assert_verbatim(delivered)

        # (2) mindie_sidecar.op_rpc dispatches with a real adapter config and a
        # real local lease, so its own admission and routing run for real.
        config_file = root / "config.json"
        config_file.write_text(json.dumps({
            "python": sys.executable, "engine_config": str(engine_config),
            "state_dir": str(root / "state"), "venv_dir": str(root / "venv"),
        }), encoding="utf-8")
        with mock.patch.dict(os.environ, {
                "MINDIE_DSH_CONFIG": str(config_file),
                "MINDIE_DSH_DATA_HOME": str(data_home)}):
            token = mindie_sidecar.admission().activate(
                session, project_root=str(root))["token"]
            for method, raw in (("query", query_raw), ("explain", explain_raw)):
                engine_result = real_explore_result(method)
                served = json.loads(json.dumps(engine_result))
                with self.subTest(adapter="op_rpc", method=method), \
                        mock.patch.object(
                            mindie_sidecar, "connection", return_value=object()), \
                        mock.patch(
                            "mindie_knowledge.loop.transport.rpc",
                            return_value=served) as rpc:
                    delivered = mindie_sidecar.op_rpc({
                        "session": session, "token": token,
                        "method": method, "arguments": dict(raw)})
                self.assertEqual(rpc.call_args.args[1], method)
                self.assertEqual(rpc.call_args.args[2]["mode"], "explore")
                assert_verbatim(delivered)


if __name__ == "__main__":
    unittest.main()
