"""Path, setup-plan, and capture-contract tests (offline)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class PathContractTests(unittest.TestCase):
    def test_published_binding_selects_staged_runtime_and_engine_in_both_hosts(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = root / "config.json"
            binding = {
                "engine_config": str(root / "engines" / "generation-2" / "engine.json"),
                "venv_dir": str(root / "runtimes" / "generation-2" / "venv"),
                "python": str(root / "runtimes" / "generation-2" / "venv" / "bin" / "python"),
            }
            config.write_text(json.dumps(binding))
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith("MINDIE_DSH_")}
            env.update(MINDIE_DSH_DATA_HOME=str(root / "data"),
                       MINDIE_DSH_CONFIG_HOME=str(root / "config"),
                       MINDIE_DSH_CONFIG=str(config))
            js = subprocess.run(
                [node, "-e", "process.stdout.write(JSON.stringify(require('./paths.cjs').paths()))"],
                cwd=ROOT, env=env, capture_output=True, text=True, check=True,
            )
            py = subprocess.run(
                [sys.executable, "-c", "import json,sys; sys.path.insert(0,'scripts'); "
                 "import mindie_paths as m; print(json.dumps({'engineConfig':str(m.engine_config()),"
                 "'venvDir':str(m.venv_dir()),'venvPython':str(m.venv_python())}))"],
                cwd=ROOT, env=env, capture_output=True, text=True, check=True,
            )
            expected = dict(engineConfig=binding["engine_config"], venvDir=binding["venv_dir"],
                            venvPython=binding["python"])
            self.assertEqual(json.loads(py.stdout), expected)
            self.assertEqual({key: json.loads(js.stdout)[key] for key in expected}, expected)

    def test_javascript_and_python_paths_agree(self) -> None:
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = dict(os.environ)
            env.update(
                {
                    "MINDIE_DSH_DATA_HOME": str(root / "data"),
                    "MINDIE_DSH_CONFIG_HOME": str(root / "config"),
                    "MINDIE_DSH_CONFIG": str(root / "config" / "custom.json"),
                    "MINDIE_DSH_ENGINE_CONFIG": str(root / "config" / "custom-engine.json"),
                    "MINDIE_DSH_STATE_DIR": str(root / "state"),
                }
            )
            js = subprocess.run(
                [node, "-e", "process.stdout.write(JSON.stringify(require('./paths.cjs').paths()))"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            py = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "import json,sys; sys.path.insert(0,'scripts'); import mindie_paths as m; "
                    "print(json.dumps({"
                    "'packageDir':str(m.PACKAGE_DIR),'dataHome':str(m.data_home()),"
                    "'configHome':str(m.config_home()),'configFile':str(m.config_file()),"
                    "'engineConfig':str(m.engine_config()),'stateDir':str(m.state_dir()),"
                    "'venvDir':str(m.venv_dir()),'venvPython':str(m.venv_python()),"
                    "'skillsDir':str(m.skills_dir()),'knowledgeScript':str(m.knowledge_script()),"
                    "'setupScript':str(m.setup_script())}))",
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            def normalized(value):
                return {key: str(Path(item).resolve()) for key, item in value.items()}

            self.assertEqual(normalized(json.loads(js.stdout)), normalized(json.loads(py.stdout)))

    def test_setup_dry_run_is_write_free(self) -> None:
        if sys.version_info < (3, 11):
            self.skipTest("setup requires Python 3.11+")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = dict(os.environ)
            env.update(
                {
                    "MINDIE_DSH_DATA_HOME": str(root / "data"),
                    "MINDIE_DSH_CONFIG_HOME": str(root / "config"),
                    "MINDIE_DSH_STATE_DIR": str(root / "state"),
                }
            )
            completed = subprocess.run(
                [sys.executable, str(SCRIPTS / "setup.py"), "--dry-run"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(
                Path(payload["paths"]["data_home"]).resolve(),
                (root / "data").resolve(),
            )
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / "config").exists())


if __name__ == "__main__":
    unittest.main()
