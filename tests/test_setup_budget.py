"""The updater's shared budget limits actual setup child processes."""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
spec = importlib.util.spec_from_file_location("dsh_setup_budget", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupBudgetTests(unittest.TestCase):
    def tearDown(self):
        setup.RUN_DEADLINE = None

    def test_native_domain_preparation_writes_two_public_feeds_without_changing_consent(self):
        from mindie_knowledge import consent_store
        from mindie_knowledge.loop.settings import from_engine_config

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config_home, data = root / 'config', root / 'data'
            config_home.mkdir()
            consent = config_home / 'mindie-consent.json'
            consent_store.record_choice(consent, 'contribute')
            before = consent.read_bytes()
            with patch.object(setup.mp, 'config_home', return_value=config_home), \
                    patch.object(setup.mp, 'data_home', return_value=data), \
                    patch.object(setup.mp, 'config_file', return_value=config_home / 'config.json'), \
                    patch.object(setup.mp, 'engine_config', return_value=config_home / 'engine.json'), \
                    patch.object(setup.mp, 'venv_python', return_value=Path(sys.executable)):
                adapter = setup.adapter_configuration()
                engine = setup.engine_configuration(sys.executable)
                self.assertEqual(engine['feeds'][0]['repository'],
                                 'mindie-agent/knowledge-vllm-ascend')
                self.assertEqual(engine['feeds'][0]['ref'], 'main')
                routes = setup.prepare_domains(adapter, engine)
                self.assertEqual(set(routes), {'vllm-ascend', 'operators'})
                self.assertEqual(routes['vllm-ascend'], adapter['engine_config'])
                route = json.loads(Path(routes['operators']).read_text())
                self.assertEqual(route['domain'], 'operators')
                self.assertEqual(route['feeds'][0]['repository'],
                                 'mindie-agent/knowledge-operators')
                self.assertEqual(route['feeds'][0]['ref'], 'main')
                self.assertFalse(from_engine_config(route).allows_capture())
                settings = json.loads(Path(route['community_config']).read_text())
                self.assertEqual(settings['repository'],
                                 'mindie-agent/knowledge-operators')
                self.assertEqual(settings['branch'], 'main')
                self.assertFalse(settings['enabled'])
                self.assertEqual(settings['consent_config'], str(consent))
                self.assertEqual(consent.read_bytes(), before)
                adapter['domain_configs'] = routes
                self.assertEqual(setup.prepare_domains(adapter, engine), routes)
                self.assertEqual(consent.read_bytes(), before)

    def test_preparation_preserves_a_custom_route_and_feed(self):
        from mindie_knowledge import consent_store

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config_home, data = root / 'config', root / 'data'
            config_home.mkdir()
            consent = config_home / 'mindie-consent.json'
            consent_store.record_choice(consent, 'contribute')
            before = consent.read_bytes()
            with patch.object(setup.mp, 'config_home', return_value=config_home), \
                    patch.object(setup.mp, 'data_home', return_value=data), \
                    patch.object(setup.mp, 'config_file', return_value=config_home / 'config.json'), \
                    patch.object(setup.mp, 'engine_config', return_value=config_home / 'engine.json'), \
                    patch.object(setup.mp, 'venv_python', return_value=Path(sys.executable)):
                adapter = setup.adapter_configuration()
                engine = setup.engine_configuration(sys.executable)
                custom = data / 'custom-operators.engine.json'
                setup.write_private(custom, dict(
                    engine, domain='operators', root=str(data / 'custom-operators'),
                    admission_path=str(data / 'custom-operators' / 'admission.sqlite3'),
                    feeds=[dict(repository='example/custom-operators',
                                ref='pinned', domain='operators')]))
                original = custom.read_bytes()
                adapter['domain_configs'] = {'operators': str(custom)}
                routes = setup.prepare_domains(adapter, engine)
                self.assertEqual(routes['operators'], str(custom))
                self.assertEqual(custom.read_bytes(), original)
                self.assertEqual(consent.read_bytes(), before)

    def test_setup_refresh_preserves_registered_independent_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            adapter = root / "config.json"
            target = root / "operators-engine.json"
            target.write_text('{"domain":"operators","root":"independent-data"}\n')
            target_bytes = target.read_bytes()
            routes = {"operators": str(target)}
            adapter.write_text(json.dumps({"version": "old", "domain_configs": routes}))
            with patch.object(setup.mp, "config_file", return_value=adapter):
                setup.write_private(adapter, setup.adapter_configuration())
            installed = json.loads(adapter.read_text())
            self.assertEqual(installed["domain_configs"], routes)
            self.assertEqual(installed["version"], setup.VERSION)
            self.assertEqual(target.read_bytes(), target_bytes)
            self.assertEqual({p.name for p in root.iterdir()},
                             {"config.json", "operators-engine.json"})

    def test_stage_cannot_outlive_remaining_setup_budget(self):
        setup.RUN_DEADLINE = time.monotonic() + 0.08
        started = time.monotonic()
        with self.assertRaisesRegex(setup.SetupError, "exceeded"):
            setup.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=900)
        self.assertLess(time.monotonic() - started, 2)

    def test_expired_setup_never_starts_another_stage(self):
        setup.RUN_DEADLINE = time.monotonic() - 1
        with self.assertRaisesRegex(setup.SetupError, "total time budget"):
            setup.run(["must-not-start-after-budget"], timeout=900)

    def test_timeout_drains_a_real_build_child_and_parent(self):
        import setup_process

        # The child inherits pipes, as a build backend does under uv.
        script = (
            "import subprocess,sys,time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "print(child.pid, flush=True)\n"
            "time.sleep(30)\n"
        )
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            setup_process.run([sys.executable, "-c", script], timeout=0.2)
        self.assertLess(time.monotonic() - started, 3)
        self.assertTrue(caught.exception.output.strip().isdigit())

    @unittest.skipUnless(os.name == "posix", "POSIX group ownership survives leader exit")
    def test_timeout_drains_build_child_after_parent_exits(self):
        import setup_process

        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "build.py"
            script.write_text(
                "import subprocess,sys\n"
                "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            with self.assertRaises(subprocess.TimeoutExpired):
                setup_process.run([sys.executable, str(script)], timeout=0.2)
            self.assertLess(time.monotonic() - started, 3)
