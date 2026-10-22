"""Native DSH graded-feedback revisions and configured-domain routing tests."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import test_knowledge_distill_tool as _fixture_module  # noqa: E402
import knowledge_mcp  # noqa: E402
import mindie_sidecar  # noqa: E402
from mindie_knowledge.loop.store import session_key  # noqa: E402
from domain_harness_support import (
    SESSION,
    UNIT_ID,
    DomainHarnessTestCase,
    graded_unit,
)


class GradedFeedbackEntryTests(unittest.TestCase):
    setUp = _fixture_module.KnowledgeDistillToolTests.setUp
    tearDown = _fixture_module.KnowledgeDistillToolTests.tearDown
    _restore_env = _fixture_module.KnowledgeDistillToolTests._restore_env

    def _install(self, unit):
        self.store.graded.install([unit])

    def _call(self, method, arguments):
        return mindie_sidecar.op_rpc({
            "session": SESSION,
            "token": self.token,
            "method": method,
            "arguments": arguments,
        })

    def _query(self):
        return self._call("query", {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        })["results"][0]

    def _feedback(self, ref, rating, reason):
        return self._call("feedback", {
            "ref": ref, "rating": rating, "reason": reason,
        })

    def _votes(self):
        rows = [dict(row) for row in self.store.db.execute("SELECT * FROM votes")]
        root = self.store.db.execute(
            "SELECT opaque FROM opaque_roots WHERE root_hash=?",
            (session_key(SESSION),),
        ).fetchone()
        return rows, (root["opaque"] if root else None)

    def test_query_feedback_records_exact_observed_revision_and_native_session(self):
        unit = graded_unit()
        self._install(unit)

        hit = self._query()
        observed = f"mindie://{self.store.domain}/units/{UNIT_ID}@{unit.revision}"
        self.assertEqual(hit["feedback_ref"], observed)
        self.assertEqual(hit["revision"], 1)

        vote = self._feedback(hit["feedback_ref"], "up",
                              "Confirmed during the fixture run.")
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertEqual(vote["revision"], 1)
        self.assertEqual(vote["rating"], "up")
        self.assertTrue(vote["publishable"])

        rows, opaque = self._votes()
        self.assertIsNotNone(opaque)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], UNIT_ID)
        self.assertEqual(rows[0]["revision"], str(unit.revision))
        self.assertEqual(rows[0]["root_opaque"], opaque)

    def test_stale_ref_keeps_the_observed_revision_after_the_unit_is_revised(self):
        original = graded_unit()
        self._install(original)

        first = self._query()
        revised = graded_unit(
            2, "Fixture graded feedback rule corrected for the next revision.\n")
        self._install(revised)
        second = self._query()
        self.assertNotEqual(first["feedback_ref"], second["feedback_ref"])
        self.assertEqual(
            second["feedback_ref"],
            f"mindie://{self.store.domain}/units/{UNIT_ID}@{revised.revision}",
        )

        old_vote = self._feedback(first["feedback_ref"], "down",
                                  "The first revision failed.")
        new_vote = self._feedback(second["feedback_ref"], "up",
                                  "The correction worked.")
        self.assertEqual(old_vote["revision"], 1)
        self.assertEqual(new_vote["revision"], 2)

        rows, _ = self._votes()
        by_revision = {row["revision"]: row for row in rows}
        self.assertEqual(set(by_revision), {str(original.revision), str(revised.revision)})
        self.assertEqual(by_revision[str(original.revision)]["rating"], "down")
        self.assertEqual(by_revision[str(revised.revision)]["rating"], "up")


class MultiDomainRoutingTests(DomainHarnessTestCase):
    def test_explicit_domain_query_selects_independent_store_and_revision(self):
        default_unit, target_unit = self._install_both()

        default_hit = self._query()
        target_hit = self._query(self.target_domain)
        self.assertEqual(default_hit["feedback_ref"],
                         self._ref(self.store.domain, default_unit))
        self.assertEqual(default_hit["revision"], 1)
        self.assertEqual(target_hit["feedback_ref"],
                         self._ref(self.target_domain, target_unit))
        self.assertEqual(target_hit["revision"], 2)

        with self.assertRaises(mindie_sidecar.SidecarError) as unknown:
            self._query("missing-domain")
        self.assertIn("unknown domain", str(unknown.exception))

    def test_old_runtime_keeps_default_and_rejects_new_explicit_routing(self):
        default_unit, target_unit = self._install_both()
        with mock.patch.object(mindie_sidecar.routing, "_selector", return_value=None):
            default_hit = self._query()
            self.assertEqual(default_hit["feedback_ref"],
                             self._ref(self.store.domain, default_unit))
            same_domain = self._call("explain", {
                "ref": self._ref(self.store.domain, default_unit),
            })
            self.assertIn("default-domain", same_domain["content"])

            with self.assertRaises(mindie_sidecar.SidecarError) as unknown:
                self._query(self.target_domain)
            self.assertIn("explicit domain routing is unavailable",
                          str(unknown.exception))
            with self.assertRaises(mindie_sidecar.SidecarError) as cross_ref:
                self._call("explain", {
                    "ref": self._ref(self.target_domain, target_unit),
                })
            self.assertIn("explicit domain routing is unavailable",
                          str(cross_ref.exception))

    def test_reference_authority_routes_explain_and_rejects_conflict(self):
        default_unit, target_unit = self._install_both()

        default_read = self._call("explain", {
            "ref": self._ref(self.store.domain, default_unit),
        })
        target_read = self._call("explain", {
            "ref": self._ref(self.target_domain, target_unit),
        })
        self.assertEqual(default_read["domain"], self.store.domain)
        self.assertIn("default-domain", default_read["content"])
        self.assertEqual(target_read["domain"], self.target_domain)
        self.assertIn("operators-domain", target_read["content"])
        self.assertEqual(target_read["revision"], 2)

        with self.assertRaises(mindie_sidecar.SidecarError) as conflict:
            self._call("explain", {
                "ref": self._ref(self.target_domain, target_unit),
                "domain": self.store.domain,
            })
        self.assertIn("conflicts", str(conflict.exception))

    def test_feedback_ref_routes_to_target_store_and_target_consent(self):
        _default_unit, target_unit = self._install_both()
        self._ensure_target_lease()
        target_ref = self._query(self.target_domain)["feedback_ref"]
        self.assertEqual(target_ref, self._ref(self.target_domain, target_unit))

        vote = self._call("feedback", {
            "ref": target_ref, "rating": "up",
            "reason": "Target-domain route confirmed.",
        })
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertEqual(vote["revision"], 2)
        self.assertTrue(vote["publishable"])
        self.assertEqual(len(self._votes(self.target_store)), 1)
        self.assertEqual(self._votes(self.store), [])

        _fixture_module.settings_mod.write(
            self.target_settings, enabled=False, repository=None,
            project_roots=[],
        )
        gated = self._call("feedback", {
            "ref": target_ref, "rating": "down",
            "reason": "Target contribution is off.",
        })
        self.assertFalse(gated["publishable"])
        target_votes = self._votes(self.target_store)
        self.assertEqual(len(target_votes), 1)
        self.assertEqual(target_votes[0]["rating"], "down")
        self.assertEqual(target_votes[0]["publishable"], 0)

    def test_target_distill_uses_target_lease_and_target_engine(self):
        self._ensure_target_lease()
        result = self._call("distill", {
            "inputs": self._inputs(),
            "task_state": "verified_milestone",
            "domain": self.target_domain,
        })
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["admitted_unit_ids"], ["unit-fixture-dsh-distill"])
        self.assertEqual(result["model_calls"], 1)
        self.assertEqual(self.calls.read_text(), "1")

        conditions = {"soc": "Ascend910B"}
        target_ids = [
            row["id"] for row in self.target_store.query(
                "graph", conditions=conditions,
            )["results"]
        ]
        default_ids = [
            row["id"] for row in self.store.query(
                "graph", conditions=conditions,
            )["results"]
        ]
        self.assertIn("unit-fixture-dsh-distill", target_ids)
        self.assertNotIn("unit-fixture-dsh-distill", default_ids)

    def test_shared_target_admission_path_reuses_default_lease(self):
        config = json.loads(self.target_engine_config.read_text(encoding="utf-8"))
        config["admission_path"] = str(self.admission_path)
        shared = self.root / "operators-shared-engine.json"
        shared.write_text(json.dumps(config), encoding="utf-8")
        lease = mindie_sidecar.routing.target_lease(
            shared, SESSION, domain=self.target_domain,
        )
        self.assertEqual(lease["token"], self.token)

    def test_missing_target_distill_lease_fails_before_model(self):
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        with self.assertRaises(mindie_sidecar.SidecarError) as missing:
            self._call("distill", {
                "inputs": self._inputs(),
                "task_state": "verified_milestone",
                "domain": self.target_domain,
            })
        self.assertIn("no active admission lease", str(missing.exception))
        self.assertFalse(self.calls.exists())

    def test_mcp_schema_and_query_route_configured_domain(self):
        default_unit, target_unit = self._install_both()
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_query", "arguments": {
                "query": "gradedfeedbackprobe",
                "include_unknown": True,
                "domain": self.target_domain,
            }},
        })
        result = response["result"]
        self.assertIs(result["isError"], False)
        payload = json.loads(result["content"][0]["text"])
        self.assertEqual(payload["results"][0]["feedback_ref"],
                         self._ref(self.target_domain, target_unit))
        self.assertNotEqual(self._ref(self.target_domain, target_unit),
                            self._ref(self.store.domain, default_unit))

        missing = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "knowledge_query", "arguments": {
                "query": "gradedfeedbackprobe", "domain": "missing-domain",
            }},
        })["result"]
        self.assertIs(missing["isError"], True)
        self.assertIn("unknown domain", missing["content"][0]["text"])

    def test_mcp_distill_routes_target_domain_and_lease(self):
        self._ensure_target_lease()
        response = knowledge_mcp.handle({
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "knowledge_distill", "arguments": {
                "inputs": self._inputs(),
                "task_state": "verified_milestone",
                "domain": self.target_domain,
            }},
        })["result"]
        self.assertIs(response["isError"], False)
        result = response["structuredContent"]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["admitted_unit_ids"], ["unit-fixture-dsh-distill"])
        self.assertEqual(self.calls.read_text(), "1")
        target_ids = [
            row["id"] for row in self.target_store.query(
                "graph", conditions={"soc": "Ascend910B"},
            )["results"]
        ]
        self.assertIn("unit-fixture-dsh-distill", target_ids)

    def test_native_host_distill_routes_target_domain_and_lease(self):
        self._ensure_target_lease()
        payload = self._run_tool_harness(domain=self.target_domain)
        self.assertTrue(payload["activated"])
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["llmCalls"], 0)
        result = payload["result"]
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["admitted_unit_ids"], ["unit-fixture-dsh-distill"])
        self.assertEqual(self.calls.read_text(), "1")

        conditions = {"soc": "Ascend910B"}
        target_ids = [
            row["id"] for row in self.target_store.query(
                "graph", conditions=conditions,
            )["results"]
        ]
        default_ids = [
            row["id"] for row in self.store.query(
                "graph", conditions=conditions,
            )["results"]
        ]
        self.assertIn("unit-fixture-dsh-distill", target_ids)
        self.assertNotIn("unit-fixture-dsh-distill", default_ids)

    def test_native_host_distill_missing_target_lease_fails_before_model(self):
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        payload = self._run_tool_harness(domain=self.target_domain)
        self.assertTrue(payload["activated"])
        self.assertIsNone(payload["result"])
        self.assertIn("no active admission lease", payload["error"])
        self.assertEqual(payload["llmCalls"], 0)
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
