"""Native DSH sidecar feedback on exact observed graded-unit revisions, including
configured independent domains routed by the shared core selector."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
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
from mindie_knowledge.graded.units import GradedUnit  # noqa: E402
from mindie_knowledge.loop.store import session_key  # noqa: E402

try:
    import mindie_knowledge

    SHARED = Path(mindie_knowledge.__file__).resolve().parents[1]
except Exception:  # pragma: no cover - CI without the union runtime
    SHARED = None

SESSION = "manual-A"
UNIT_ID = "unit-graded-feedback-entry"
TARGET_DOMAIN = "operators"
HARNESS = ROOT / "tests" / "host_domain_harness.mjs"
TOOL_HARNESS = ROOT / "tests" / "host_tool_harness.mjs"
FIXTURE_WORKER = ROOT / "tests" / "fixtures" / "graded_worker.py"


def graded_unit(revision=1, body="Fixture graded feedback rule for the observed revision.\n"):
    return GradedUnit.from_parts({
        "schema": "mindie-graded-unit/1",
        "id": UNIT_ID,
        "tier": "L2",
        "category": "sop",
        "evidence_strength": "single_observation",
        "domain_path": "ops/graded-feedback",
        "revision": revision,
        "inherits_from": [],
        "title": "Synthetic graded feedback entry",
        "triggers": {
            "error_signatures": ["SYNTHETIC_GRADED_FEEDBACK"],
            "keywords": ["gradedfeedbackprobe"],
        },
        "conditions": {"soc": ["Ascend910B"]},
        "provenance": {
            "source_type": "session_distill",
            "source_locator": "fixture://graded-feedback-entry",
            "source_digest": "c" * 64,
            "evidence_refs": ["fixture-observation"],
            "supersedes": [],
        },
    }, body, f"ops/graded-feedback/{UNIT_ID}.md")


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
        observed = f"mindie://{self.store.domain}/units/{UNIT_ID}@{unit.content_sha256}"
        self.assertEqual(hit["feedback_ref"], observed)
        self.assertEqual(hit["revision"], 1)

        vote = self._feedback(hit["feedback_ref"], "up",
                              "Confirmed during the fixture run.")
        self.assertEqual(vote["unit_id"], UNIT_ID)
        self.assertEqual(vote["revision"], 1)
        self.assertEqual(vote["content_digest"], unit.metadata["content_digest"])
        self.assertEqual(vote["rating"], "up")
        self.assertTrue(vote["publishable"])

        rows, opaque = self._votes()
        self.assertIsNotNone(opaque)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["entry_id"], UNIT_ID)
        self.assertEqual(rows[0]["revision"], unit.content_sha256)
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
            f"mindie://{self.store.domain}/units/{UNIT_ID}@{revised.content_sha256}",
        )

        old_vote = self._feedback(first["feedback_ref"], "down",
                                  "The first revision failed.")
        new_vote = self._feedback(second["feedback_ref"], "up",
                                  "The correction worked.")
        self.assertEqual((old_vote["revision"], old_vote["content_digest"]),
                         (1, original.metadata["content_digest"]))
        self.assertEqual((new_vote["revision"], new_vote["content_digest"]),
                         (2, revised.metadata["content_digest"]))

        rows, _ = self._votes()
        by_revision = {row["revision"]: row for row in rows}
        self.assertEqual(set(by_revision), {original.content_sha256, revised.content_sha256})
        self.assertEqual(by_revision[original.content_sha256]["rating"], "down")
        self.assertEqual(by_revision[revised.content_sha256]["rating"], "up")


class MultiDomainRoutingTests(unittest.TestCase):
    _base_setUp = _fixture_module.KnowledgeDistillToolTests.setUp
    _base_tearDown = _fixture_module.KnowledgeDistillToolTests.tearDown
    _restore_env = _fixture_module.KnowledgeDistillToolTests._restore_env

    @classmethod
    def setUpClass(cls) -> None:
        _fixture_module.KnowledgeDistillToolTests.setUpClass()
        if shutil.which("node") is None:  # pragma: no cover - node is the native host
            raise unittest.SkipTest("node is unavailable")

    def setUp(self) -> None:
        self._base_setUp()
        self.addCleanup(self._stop_target)
        self._start_target()

    def tearDown(self) -> None:
        self._base_tearDown()

    def _start_target(self) -> None:
        self.target_domain = TARGET_DOMAIN
        self.target_knowledge = self.root / "knowledge-operators"
        self.target_settings = self.root / "operators-community.json"
        target_consent = self.root / "operators-community.consent.json"
        _fixture_module.consent_store.record_choice(target_consent, "contribute")
        _fixture_module.settings_mod.write(
            self.target_settings, enabled=True,
            repository="mindie-agent/knowledge-operators",
            project_roots=[self.project], idle_seconds=300,
            consent_config=str(target_consent),
        )
        self.target_admission_path = self.root / "operators-admission.sqlite3"
        self.target_admission = _fixture_module.Admission(self.target_admission_path)
        self.target_store = _fixture_module.Store(
            self.target_knowledge, self.target_domain,
        )
        self.target_engine_config = self.root / "operators-engine.json"
        target_config = {
            "root": str(self.target_knowledge),
            "domain": self.target_domain,
            "redactor_executable": str(self.scanner),
            "community_config": str(self.target_settings),
            "transcript_adapter": str(ROOT / "scripts" / "dsh_transcript.py"),
            "summary_command": [sys.executable, str(FIXTURE_WORKER)],
            "admission_path": str(self.target_admission_path),
        }
        self.target_engine_config.write_text(
            json.dumps(target_config), encoding="utf-8",
        )
        self.target_engine = _fixture_module.Engine(
            self.target_store, settings_path=str(self.target_settings),
            admission=self.target_admission,
            redactor_executable=str(self.scanner),
            summary_command=target_config["summary_command"],
        )
        self.target_engine.start()
        self.target_consumer = _fixture_module.lifecycle.acquire_consumer(
            str(self.target_engine_config), target_config,
        )
        self.target_service = _fixture_module.Service(
            self.target_engine, admission=self.target_admission,
            config_path=str(self.target_engine_config),
            config_fingerprint=_fixture_module.config_fingerprint(
                self.target_engine_config, target_config,
            ),
        )
        connection = _fixture_module.connection_path(target_config)
        connection.parent.mkdir(parents=True, exist_ok=True)
        connection.write_text(
            json.dumps(self.target_service.connection), encoding="utf-8",
        )
        self.target_thread = threading.Thread(
            target=self.target_service.http.serve_forever, daemon=True,
        )
        self.target_thread.start()

        adapter = json.loads(self.adapter_config.read_text(encoding="utf-8"))
        adapter["domain_configs"] = {
            self.target_domain: str(self.target_engine_config),
        }
        self.adapter_config.write_text(json.dumps(adapter), encoding="utf-8")
        (self.root / "state").mkdir(exist_ok=True)
        venv = self.root / "venv"
        if not venv.exists():
            venv.symlink_to(
                Path(sys.executable).parent.parent, target_is_directory=True,
            )

    def _stop_target(self) -> None:
        service = getattr(self, "target_service", None)
        if service is not None:
            try:
                service.http.shutdown()
            except Exception:
                pass
            try:
                service.http.server_close()
            except Exception:
                pass
        thread = getattr(self, "target_thread", None)
        if thread is not None:
            thread.join(timeout=2)
        engine = getattr(self, "target_engine", None)
        if engine is not None:
            engine.shutdown()
        consumer = getattr(self, "target_consumer", None)
        if consumer is not None:
            consumer.release()
        store = getattr(self, "target_store", None)
        if store is not None:
            store.close()

    def _call(self, method, arguments):
        return mindie_sidecar.op_rpc({
            "session": SESSION,
            "token": self.token,
            "method": method,
            "arguments": arguments,
        })

    def _inputs(self, *, outcome="observed"):
        return _fixture_module.KnowledgeDistillToolTests._inputs(self, outcome=outcome)

    def _ensure_target_lease(self):
        lease = self.target_admission.active_lease(SESSION)
        if lease is None:
            lease = self.target_admission.activate(
                SESSION, project_root=str(self.project),
            )
        return lease

    def _install_both(self, *, target_revision=2, target_body=None):
        default_unit = graded_unit(
            1, "Fixture default-domain graded feedback rule.\n",
        )
        target_unit = graded_unit(
            target_revision,
            target_body or "Fixture operators-domain graded feedback rule.\n",
        )
        self.store.graded.install([default_unit])
        self.target_store.graded.install([target_unit])
        return default_unit, target_unit

    @staticmethod
    def _ref(domain, unit):
        return f"mindie://{domain}/units/{UNIT_ID}@{unit.content_sha256}"

    def _query(self, domain=None):
        arguments = {
            "query": "gradedfeedbackprobe",
            "include_unknown": True,
            "limit": 5,
        }
        if domain is not None:
            arguments["domain"] = domain
        return self._call("query", arguments)["results"][0]

    def _votes(self, store):
        return [dict(row) for row in store.db.execute("SELECT * FROM votes")]

    @staticmethod
    def _stored_token(admission_path, session):
        with sqlite3.connect(admission_path) as database:
            row = database.execute(
                "SELECT token FROM leases WHERE session=?", (session,),
            ).fetchone()
        return row[0] if row else None

    def _harness_env(self, **extra):
        env = dict(os.environ)
        if SHARED is not None:
            paths = [str(SHARED)]
            if existing := env.get("PYTHONPATH"):
                paths.append(existing)
            env["PYTHONPATH"] = os.pathsep.join(paths)
        env.update(extra)
        return env

    def _run_domain_harness(self, operations, *, with_raw=False):
        env = self._harness_env(
            DOMAIN_OPERATIONS=json.dumps(operations),
            DOMAIN_CWD=str(self.project),
            DOMAIN_SESSION=SESSION,
        )
        completed = subprocess.run(
            [shutil.which("node"), str(HARNESS)], env=env,
            capture_output=True, text=True, timeout=90,
        )
        if completed.returncode != 0:
            self.fail(f"native domain harness failed: {completed.stderr or completed.stdout}")
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        return (payload, completed.stdout) if with_raw else payload

    def _run_tool_harness(self, *, domain=None):
        env = self._harness_env(
            DISTILL_DECISION="{}",
            DISTILL_INPUTS=json.dumps(self._inputs()),
            DISTILL_CWD=str(self.project),
        )
        if domain is not None:
            env["DISTILL_DOMAIN"] = domain
        completed = subprocess.run(
            [shutil.which("node"), str(TOOL_HARNESS)], env=env,
            capture_output=True, text=True, timeout=90,
        )
        if completed.returncode != 0:
            self.fail(f"native distill harness failed: {completed.stderr or completed.stdout}")
        return json.loads(completed.stdout.strip().splitlines()[-1])

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

    def test_native_entry_default_init_does_not_activate_target(self):
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {"op": "init"}},
        ])
        result = payload["results"][0]
        self.assertTrue(result["ok"])
        value = result["value"]
        self.assertTrue(value["activated"])
        self.assertNotIn("domain_activation", value)
        self.assertNotIn("domains", value)
        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)
        self.assertIsNone(self.target_admission.active_lease(SESSION))

    def test_native_entry_init_activates_target_and_routes_lease(self):
        _default_unit, target_unit = self._install_both()
        target_ref = self._ref(self.target_domain, target_unit)
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "init", "domain": self.target_domain,
            }},
            {"tool": "knowledge_query", "args": {
                "query": "gradedfeedbackprobe", "include_unknown": True,
                "limit": 5, "domain": self.target_domain,
            }},
            {"tool": "knowledge_feedback", "args": {
                "ref": target_ref, "rating": "up",
                "reason": "Native target activation confirmed.",
            }},
            {"tool": "knowledge_distill", "args": {
                "inputs": self._inputs(),
                "task_state": "verified_milestone",
                "domain": self.target_domain,
            }},
        ])
        self.assertTrue(payload["activated"])
        self.assertEqual(payload["llmCalls"], 0)
        results = payload["results"]
        self.assertTrue(all(result["ok"] for result in results))

        activation = results[0]["value"]["domain_activation"]
        self.assertEqual(activation["domain"], self.target_domain)
        self.assertTrue(activation["generation"])
        self.assertNotIn("token", activation)
        self.assertNotIn("token", activation["lease"])
        self.assertEqual(activation["lease"]["session"], SESSION)
        self.assertNotIn("domains", results[0]["value"])

        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)
        target_lease = self.target_admission.active_lease(SESSION)
        self.assertIsNotNone(target_lease)
        self.assertNotEqual(target_lease["token"], self.token)
        self.assertEqual(target_lease["session"], SESSION)
        self.assertEqual(target_lease["root_session"], SESSION)
        self.assertEqual(
            Path(target_lease["project_root"]).resolve(),
            self.project.resolve(),
        )

        self.assertEqual(
            results[1]["value"]["results"][0]["feedback_ref"], target_ref,
        )
        self.assertEqual(results[2]["value"]["revision"], 2)
        self.assertTrue(results[2]["value"]["publishable"])
        self.assertEqual(results[3]["value"]["status"], "applied")
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
        self.assertEqual(len(self._votes(self.target_store)), 1)
        self.assertEqual(self._votes(self.store), [])

    def test_sidecar_target_activation_keeps_default_token_and_mints_distinct(self):
        result = mindie_sidecar.op_activate({
            "session": SESSION,
            "cwd": str(self.project),
            "domain": self.target_domain,
        })
        self.assertEqual(result["token"], self.token)
        self.assertNotIn("token", result["domain_activation"])
        target_token = result["domain_activation"]["lease"]["token"]
        self.assertNotEqual(target_token, self.token)
        self.assertEqual(
            result["domain_activation"]["lease"]["session"], SESSION,
        )
        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)
        target_lease = self.target_admission.active_lease(SESSION)
        self.assertEqual(target_lease["token"], target_token)

    def test_native_entry_init_target_disabled_fails_without_lease(self):
        _fixture_module.settings_mod.write(
            self.target_settings, enabled=False, repository=None,
            project_roots=[],
        )
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "init", "domain": self.target_domain,
            }},
        ])
        result = payload["results"][0]
        self.assertFalse(result["ok"])
        self.assertIn("not authorized", result["error"])
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)

    def test_native_entry_init_target_out_of_scope_fails_without_lease(self):
        outside = self.root / "outside"
        outside.mkdir()
        _fixture_module.settings_mod.write(
            self.target_settings, enabled=True,
            repository="mindie-agent/knowledge-operators",
            project_roots=[outside],
        )
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "init", "domain": self.target_domain,
            }},
        ])
        result = payload["results"][0]
        self.assertFalse(result["ok"])
        self.assertIn("outside", result["error"])
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)

    def test_native_entry_init_ignores_caller_identity_arguments(self):
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "init",
                "domain": self.target_domain,
                "session": "attacker",
                "project_root": str(self.root / "attacker"),
                "root_session": "attacker",
                "token": "forged",
            }},
        ])
        self.assertTrue(payload["results"][0]["ok"])
        target_lease = self.target_admission.active_lease(SESSION)
        self.assertIsNotNone(target_lease)
        self.assertEqual(target_lease["session"], SESSION)
        self.assertEqual(target_lease["root_session"], SESSION)
        self.assertEqual(
            Path(target_lease["project_root"]).resolve(),
            self.project.resolve(),
        )
        self.assertNotEqual(target_lease["token"], "forged")
        default_lease = _fixture_module.Admission(
            self.admission_path,
        ).active_lease(SESSION)
        self.assertEqual(default_lease["token"], self.token)

    def test_native_entry_domain_is_init_only(self):
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "status", "domain": self.target_domain,
            }},
        ])
        result = payload["results"][0]
        self.assertFalse(result["ok"])
        self.assertIn("only for mindie_entry op=init", result["error"])
        self.assertIsNone(self.target_admission.active_lease(SESSION))

    def test_native_entry_deactivate_revokes_both_admissions_and_preserves_other(self):
        default_admission = _fixture_module.Admission(self.admission_path)
        other_default = default_admission.activate(
            "other", project_root=str(self.project),
        )
        other_target = self.target_admission.activate(
            "other", project_root=str(self.project),
        )
        payload, raw = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {
                "op": "init", "domain": self.target_domain,
            }},
            {"tool": "mindie_entry", "args": {"op": "deactivate"}},
            {"tool": "mindie_entry", "args": {"op": "status"}},
        ], with_raw=True)
        results = payload["results"]
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        self.assertTrue(results[2]["ok"])
        deactivation = results[1]["value"]
        self.assertEqual(deactivation["status"], "inactive")
        self.assertTrue(deactivation["deactivated"])
        self.assertEqual(
            {item["status"] for item in deactivation["domains"]},
            {"inactive"},
        )
        self.assertFalse(results[2]["value"]["activated"])

        self.assertIsNone(default_admission.active_lease(SESSION))
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertEqual(
            default_admission.active_lease("other")["token"],
            other_default["token"],
        )
        self.assertEqual(
            self.target_admission.active_lease("other")["token"],
            other_target["token"],
        )

        target_token = self._stored_token(self.target_admission_path, SESSION)
        self.assertIsNotNone(target_token)
        self.assertNotEqual(target_token, self.token)
        self.assertNotIn(self.token, raw)
        self.assertNotIn(target_token, raw)
        self.assertNotIn('"token"', raw)

    def test_native_entry_deactivate_revokes_capture_disabled_target(self):
        self.target_admission.activate(SESSION, project_root=str(self.project))
        _fixture_module.settings_mod.write(
            self.target_settings, enabled=False, repository=None,
            project_roots=[],
        )
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {"op": "init"}},
            {"tool": "mindie_entry", "args": {"op": "deactivate"}},
            {"tool": "mindie_entry", "args": {"op": "status"}},
        ])
        results = payload["results"]
        self.assertTrue(results[1]["ok"])
        self.assertEqual(results[1]["value"]["status"], "inactive")
        self.assertIsNone(
            _fixture_module.Admission(self.admission_path).active_lease(SESSION),
        )
        self.assertIsNone(self.target_admission.active_lease(SESSION))
        self.assertFalse(results[2]["value"]["activated"])

    def test_native_entry_deactivate_partial_keeps_host_state(self):
        self.target_admission.activate(SESSION, project_root=str(self.project))
        target_config = json.loads(
            self.target_engine_config.read_text(encoding="utf-8"),
        )
        target_config["admission_path"] = "relative-admission.sqlite3"
        self.target_engine_config.write_text(
            json.dumps(target_config), encoding="utf-8",
        )
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {"op": "init"}},
            {"tool": "mindie_entry", "args": {"op": "deactivate"}},
            {"tool": "mindie_entry", "args": {"op": "status"}},
        ])
        results = payload["results"]
        self.assertTrue(results[0]["ok"])
        self.assertFalse(results[1]["ok"])
        self.assertIn("partial", results[1]["error"])
        self.assertIn(self.target_domain, results[1]["error"])
        self.assertTrue(results[2]["ok"])
        self.assertTrue(results[2]["value"]["activated"])
        self.assertIsNone(
            _fixture_module.Admission(self.admission_path).active_lease(SESSION),
        )
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

    def test_native_entry_deactivate_without_domain_configs_keeps_old_behavior(self):
        self.target_admission.activate(SESSION, project_root=str(self.project))
        adapter = json.loads(self.adapter_config.read_text(encoding="utf-8"))
        adapter.pop("domain_configs", None)
        self.adapter_config.write_text(json.dumps(adapter), encoding="utf-8")
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {"op": "init"}},
            {"tool": "mindie_entry", "args": {"op": "deactivate"}},
            {"tool": "mindie_entry", "args": {"op": "status"}},
        ])
        results = payload["results"]
        self.assertTrue(results[1]["ok"])
        self.assertTrue(results[1]["value"]["deactivated"])
        self.assertNotIn("domains", results[1]["value"])
        self.assertIsNone(
            _fixture_module.Admission(self.admission_path).active_lease(SESSION),
        )
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))
        self.assertFalse(results[2]["value"]["activated"])

    def test_missing_registered_deactivation_helper_fails_without_revoking(self):
        self.target_admission.activate(SESSION, project_root=str(self.project))
        import mindie_knowledge.loop as loop_package
        from mindie_knowledge.loop import domain_routes as domain_routes_module

        with mock.patch.dict(sys.modules, {
            "mindie_knowledge.loop.domain_routes": None,
        }):
            if getattr(loop_package, "domain_routes", None) is not None:
                delattr(loop_package, "domain_routes")
            try:
                with self.assertRaises(mindie_sidecar.SidecarError) as missing:
                    mindie_sidecar.op_deactivate({"session": SESSION})
            finally:
                setattr(loop_package, "domain_routes", domain_routes_module)
        self.assertIn(
            "registered-domain deactivation is unavailable",
            str(missing.exception),
        )
        self.assertIsNotNone(
            _fixture_module.Admission(self.admission_path).active_lease(SESSION),
        )
        self.assertIsNotNone(self.target_admission.active_lease(SESSION))

    def test_internal_import_error_is_not_masked_as_old_core(self):
        import builtins

        real_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "mindie_knowledge.loop.domain_routes":
                raise ImportError(
                    "internal dependency failed",
                    name="some_internal_dependency",
                )
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=failing_import):
            with self.assertRaises(ImportError) as failure:
                mindie_sidecar._registered_domain_deactivator()
        self.assertNotIsInstance(failure.exception, mindie_sidecar.SidecarError)
        self.assertEqual(failure.exception.name, "some_internal_dependency")

    def test_target_reads_and_feedback_never_implicitly_activate(self):
        _default_unit, target_unit = self._install_both()
        hit = self._query(self.target_domain)
        self.assertEqual(hit["revision"], 2)
        self.assertEqual(hit["feedback_ref"],
                         self._ref(self.target_domain, target_unit))
        self.assertIsNone(self.target_admission.active_lease(SESSION))

        with self.assertRaises(mindie_sidecar.SidecarError) as missing:
            self._call("feedback", {
                "ref": hit["feedback_ref"], "rating": "up",
                "reason": "No implicit target activation.",
            })
        self.assertIn("no active admission lease", str(missing.exception))
        self.assertIsNone(self.target_admission.active_lease(SESSION))

    def test_native_host_entry_routes_query_explain_feedback_and_unknown(self):
        _default_unit, target_unit = self._install_both()
        self._ensure_target_lease()
        target_ref = self._ref(self.target_domain, target_unit)
        operations = [
            {"tool": "knowledge_query", "args": {
                "query": "gradedfeedbackprobe", "include_unknown": True, "limit": 5,
            }},
            {"tool": "knowledge_query", "args": {
                "query": "gradedfeedbackprobe", "include_unknown": True,
                "limit": 5, "domain": self.target_domain,
            }},
            {"tool": "knowledge_explain", "args": {"ref": target_ref}},
            {"tool": "knowledge_feedback", "args": {
                "ref": target_ref, "rating": "up",
                "reason": "Native host domain route confirmed.",
            }},
            {"tool": "knowledge_query", "args": {
                "query": "gradedfeedbackprobe", "domain": "missing-domain",
            }},
        ]
        payload = self._run_domain_harness(operations)
        self.assertTrue(payload["activated"])
        self.assertEqual(payload["llmCalls"], 0)
        results = payload["results"]
        self.assertTrue(results[0]["ok"])
        self.assertTrue(results[1]["ok"])
        self.assertTrue(results[2]["ok"])
        self.assertTrue(results[3]["ok"])
        self.assertFalse(results[4]["ok"])
        self.assertEqual(results[0]["value"]["results"][0]["revision"], 1)
        self.assertEqual(results[1]["value"]["results"][0]["revision"], 2)
        self.assertEqual(results[1]["value"]["results"][0]["feedback_ref"], target_ref)
        self.assertEqual(results[2]["value"]["domain"], self.target_domain)
        self.assertIn("operators-domain", results[2]["value"]["content"])
        self.assertEqual(results[3]["value"]["revision"], 2)
        self.assertTrue(results[3]["value"]["publishable"])
        self.assertIn("unknown domain", results[4]["error"])
        self.assertEqual(len(self._votes(self.target_store)), 1)
        self.assertEqual(self._votes(self.store), [])


if __name__ == "__main__":
    unittest.main()
