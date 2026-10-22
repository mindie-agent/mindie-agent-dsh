"""Shared native host/domain harness setup for graded feedback test modules.

[Role] Provides the target-domain engine, admission, HTTP service, and native
Harness runners used by split graded-feedback test modules.
[Invariants] Each test owns isolated temporary domains, leases, and sockets;
tokens stay in their own admissions and never leak into harness output.
[Timeouts & Locks] Target HTTP threads join within 2s; native harness
subprocesses carry a 90s timeout.
[Side-Effect Policy] Test-only temporary directories and loopback services;
no host configuration or external state is modified.
"""
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

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT / "tests", ROOT / "scripts"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

import test_knowledge_distill_tool as _fixture_module  # noqa: E402
import mindie_sidecar  # noqa: E402
from mindie_knowledge.graded.units import GradedUnit  # noqa: E402

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



class DomainHarnessTestCase(unittest.TestCase):
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
        return f"mindie://{domain}/units/{UNIT_ID}@{unit.revision}"

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
