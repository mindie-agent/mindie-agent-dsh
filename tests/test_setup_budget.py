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
                self.assertEqual(adapter['components']['mindie-knowledge'], '0.13.13')
                self.assertEqual(adapter['components']['remote-dev'], '0.11.9')
                self.assertEqual(adapter['components']['mindie-coordinator'], '0.7.8')
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

    def test_windows_process_spawn_and_timeout_cleanup_pass_create_no_window(self):
        import setup_process

        popen_calls = []
        run_calls = []

        class FakeProcess:
            pid = 4242
            returncode = 1

            def __init__(self):
                self.calls = 0

            def communicate(self, input=None, timeout=None):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired(["cmd"], timeout or 1.0)
                return ("partial-out", "partial-err")

        def fake_popen(command, **kwargs):
            popen_calls.append((command, kwargs))
            return FakeProcess()

        def fake_run(command, **kwargs):
            run_calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "", "")

        with patch.object(setup_process.os, "name", "nt"), \
                patch.object(setup_process.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                patch.object(setup_process.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200, create=True), \
                patch.object(setup_process.subprocess, "Popen", side_effect=fake_popen), \
                patch.object(setup_process.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(subprocess.TimeoutExpired):
                setup_process.run(["uv", "sync"], timeout=1.0)

        self.assertEqual(len(popen_calls), 1)
        self.assertEqual(popen_calls[0][1]["creationflags"], 0x08000000 | 0x00000200)
        self.assertEqual(len(run_calls), 1)
        self.assertEqual(run_calls[0][0], ["taskkill", "/PID", "4242", "/T", "/F"])
        self.assertEqual(run_calls[0][1]["creationflags"], 0x08000000)

    def test_sync_runtime_appends_to_existing_git_config_count(self):
        captured_env = {}

        def fake_run(argv, *, timeout, env=None, cwd=None):
            if env is not None:
                captured_env.update(env)
            return subprocess.CompletedProcess(argv, 0, "", "")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.dict(
                os.environ,
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.sslBackend",
                    "GIT_CONFIG_VALUE_0": "schannel",
                },
                clear=False,
            ), patch.object(setup, "runtime_dir", return_value=root / "venv"), \
                    patch.object(setup, "runtime_python", return_value=Path(sys.executable)), \
                    patch.object(setup.mp, "data_home", return_value=root / "data"), \
                    patch.object(setup, "run", side_effect=fake_run):
                setup.sync_runtime("3.13", timeout=30)

        self.assertEqual(captured_env["GIT_CONFIG_COUNT"], "4")
        pairs = {
            captured_env[f"GIT_CONFIG_KEY_{idx}"]: captured_env[f"GIT_CONFIG_VALUE_{idx}"]
            for idx in range(4)
        }
        self.assertEqual(pairs["http.sslBackend"], "schannel")
        self.assertEqual(pairs["core.longpaths"], "true")
        self.assertEqual(pairs["core.autocrlf"], "false")
        self.assertEqual(pairs["credential.https://github.com.provider"], "github")

    def test_sharing_subcommand_lifecycle_mutual_exclusivity_and_validation_guard(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config_home = root / "config"
            data_home = root / "data"
            workspace = root / "workspace"
            config_home.mkdir()
            data_home.mkdir()
            workspace.mkdir()
            config_path = config_home / "config.json"

            # 1. Mutual exclusivity between --status / --enable / --disable
            with self.assertRaises(SystemExit):
                setup.main(["sharing", "--status", "--enable", "--config", str(config_path)])

            with patch.dict(
                os.environ,
                {
                    "MINDIE_DSH_CONFIG_HOME": str(config_home),
                    "MINDIE_DSH_DATA_HOME": str(data_home),
                    "MINDIE_DIAGNOSTICS_ROOT": str(root / "logs"),
                },
                clear=False,
            ), patch("mindie_diagnostics.reporting_commands.ensure",
                     return_value={"status": "degraded", "category": "test_no_native_service"}), \
                    patch("mindie_sidecar._existing_fork", return_value="alice/knowledge-vllm-ascend"):
                # 2. Initial sharing --status is unset and needs onboarding prompt
                out = io.StringIO()
                with redirect_stdout(out):
                    rc = setup.main(["sharing", "--status", "--config", str(config_path)])
                self.assertEqual(rc, 0)
                status = json.loads(out.getvalue())
                self.assertEqual(status["schema"], "mindie-sharing-status/1")
                self.assertEqual(status["harness"], "dsh")
                self.assertIsNone(status["consent_choice"])
                self.assertFalse(status["sharing_enabled"])
                self.assertTrue(status["needs_onboarding_prompt"])

                # 3. Invalid --repository fails BEFORE writing mindie-consent.json
                err = io.StringIO()
                with redirect_stderr(err):
                    rc_bad = setup.main(
                        [
                            "sharing",
                            "--enable",
                            "--config",
                            str(config_path),
                            "--repository",
                            "invalid-repo-without-slash",
                            "--fork",
                            "alice/knowledge-vllm-ascend",
                            "--account",
                            "alice",
                            "--project-root",
                            str(workspace),
                        ]
                    )
                self.assertEqual(rc_bad, 1)
                self.assertFalse((config_home / "mindie-consent.json").exists())

                # 4. Valid sharing --enable writes community + consent
                out_enable = io.StringIO()
                with redirect_stdout(out_enable):
                    rc_en = setup.main(
                        [
                            "sharing",
                            "--enable",
                            "--config",
                            str(config_path),
                            "--fork",
                            "alice/knowledge-vllm-ascend",
                            "--account",
                            "alice",
                            "--project-root",
                            str(workspace),
                        ]
                    )
                self.assertEqual(rc_en, 0)
                enabled = json.loads(out_enable.getvalue())
                self.assertEqual(enabled["consent_choice"], "contribute")
                self.assertTrue(enabled["sharing_enabled"])
                self.assertFalse(enabled["needs_onboarding_prompt"])
                self.assertEqual(enabled["fork"], "alice/knowledge-vllm-ascend")
                self.assertEqual(enabled["account"], "alice")
                self.assertEqual(enabled["project_roots"], [str(workspace)])

                # 5. sharing --disable records disabled and turns off sharing
                out_disable = io.StringIO()
                with redirect_stdout(out_disable):
                    rc_dis = setup.main(
                        ["sharing", "--disable", "--config", str(config_path)]
                    )
                self.assertEqual(rc_dis, 0)
                disabled = json.loads(out_disable.getvalue())
                self.assertEqual(disabled["consent_choice"], "disabled")
                self.assertFalse(disabled["sharing_enabled"])
                self.assertFalse(disabled["needs_onboarding_prompt"])
