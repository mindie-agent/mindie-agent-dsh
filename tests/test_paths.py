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

    def test_capture_stub_fails_loudly(self) -> None:
        module = load_module("capture_stub", SCRIPTS / "dsh_capture_unavailable.py")
        with self.assertRaises(NotImplementedError):
            module.read_material("anything")


if __name__ == "__main__":
    unittest.main()
