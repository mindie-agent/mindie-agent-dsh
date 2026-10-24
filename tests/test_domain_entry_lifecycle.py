"""Native mindie_entry init/deactivate domain lifecycle tests."""

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
import mindie_sidecar  # noqa: E402
from domain_harness_support import (  # noqa: E402
    SESSION,
    DomainHarnessTestCase,
)


class DomainEntryLifecycleTests(DomainHarnessTestCase):
    def test_plugin_reload_restores_only_the_same_manual_session(self):
        self._install_both()
        admission = _fixture_module.Admission(self.admission_path)
        admission.deactivate(SESSION)
        self._run_domain_harness([{"tool": "mindie_entry", "args": {"op": "status"}}])
        token = admission.knowledge_lease(SESSION)["token"]
        operations = [
            {"tool": "mindie_entry", "args": {"op": "status"}},
            {"tool": "knowledge_query", "args": {"query": "gradedfeedbackprobe"}},
        ]
        # Each harness invocation loads a new native plugin instance.
        resumed = self._run_domain_harness(operations, activate=False)
        self.assertTrue(resumed["results"][0]["value"]["activated"])
        self.assertTrue(resumed["results"][1]["ok"], resumed)
        self.assertEqual(admission.knowledge_lease(SESSION)["token"], token)
        fresh = self._run_domain_harness(operations, activate=False, session="new-session")
        self.assertFalse(fresh["results"][0]["value"]["activated"])
        self.assertFalse(fresh["results"][1]["ok"], fresh)
        self.assertEqual(fresh["llmCalls"], 0)
        self.assertIsNone(admission.knowledge_lease("new-session"))

    def test_factual_status_without_activation_does_not_enable_knowledge(self):
        admission = _fixture_module.Admission(self.admission_path)
        admission.deactivate(SESSION)
        payload = self._run_domain_harness([
            {"tool": "mindie_entry", "args": {"op": "status"}},
            {"tool": "mindie_entry", "args": {"op": "sharing-status"}},
            {"tool": "knowledge_query", "args": {"query": "gradedfeedbackprobe"}},
        ], activate=False)
        self.assertFalse(payload["activated"])
        self.assertEqual(payload["llmCalls"], 0)
        for result in payload["results"][:2]:
            self.assertTrue(result["ok"], result)
            self.assertFalse(result["value"]["activated"])
            self.assertNotIn("next", result["value"])
        self.assertFalse(payload["results"][2]["ok"])
        self.assertIn("not activated", payload["results"][2]["error"])
        self.assertIsNone(admission.knowledge_lease(SESSION))

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
        self.assertNotIn("next", results[2]["value"])

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
        self.assertTrue(results[2]["ok"])
        self.assertFalse(results[2]["value"]["activated"])

    def test_native_entry_deactivate_partial_reports_actual_default_authority(self):
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
        self.assertFalse(results[2]["value"]["activated"])
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
        self.assertTrue(results[2]["ok"])
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
