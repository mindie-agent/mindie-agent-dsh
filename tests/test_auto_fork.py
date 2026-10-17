"""Auto-fork contribution setup (skipped without the knowledge runtime)."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

try:
    from mindie_knowledge import consent_store  # noqa: F401
except Exception:  # pragma: no cover - CI without the pinned runtime
    consent_store = None


class ForkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if consent_store is None:
            raise unittest.SkipTest("mindie-knowledge runtime is unavailable")

    def test_default_upstream_is_forked_under_the_account(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            marker = root / "forked"
            gh = bin_dir / "gh"
            gh.write_text(
                "#!/bin/sh\n"
                'if [ "$1 $2" = "api user" ]; then echo testuser; exit 0; fi\n'
                'if [ "$1 $2" = "repo list" ]; then echo \'[{"nameWithOwner":"testuser/vaws-knowledge-corpus","parent":{"nameWithOwner":"mindie-agent/knowledge-vllm-ascend"}}]\'; exit 0; fi\n'
                'if [ "$1 $2" = "repo view" ]; then exit 1; fi\n'
                'if [ "$1 $2" = "repo fork" ]; then touch '
                f'"{marker}"; echo testuser/knowledge-vllm-ascend; exit 0; fi\n'
                "exit 1\n"
            )
            gh.chmod(gh.stat().st_mode | stat.S_IXUSR)
            config_dir = root / "config"
            config_dir.mkdir()
            engine = config_dir / "engine.json"
            engine.write_text(
                json.dumps({"community_config": str(config_dir / "mindie-community.json")})
            )
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "python": os.sys.executable,
                        "engine_config": str(engine),
                        "state_dir": str(root / "state"),
                        "venv_dir": str(root / "venv"),
                    }
                )
            )
            env = {
                "MINDIE_DSH_CONFIG_HOME": str(config_dir),
                "MINDIE_DSH_DATA_HOME": str(root / "data"),
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            }
            old = {key: os.environ.get(key) for key in env}
            try:
                os.environ.update(env)
                import sys

                if str(SCRIPTS) not in sys.path:
                    sys.path.insert(0, str(SCRIPTS))
                import mindie_sidecar

                result = mindie_sidecar.op_configure(
                    {"choice": "contribute", "cwd": str(root), "account": "testuser"}
                )
            finally:
                for key, value in old.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertTrue(result["sharing"]["enabled"])
            self.assertEqual(result["sharing"]["repository"], "mindie-agent/knowledge-vllm-ascend")
            self.assertEqual(result["sharing"]["fork"], "testuser/vaws-knowledge-corpus")
            self.assertFalse(marker.is_file())
            document = json.loads((root / "config" / "mindie-community.json").read_text())
            self.assertEqual(document["fork"], "testuser/vaws-knowledge-corpus")


if __name__ == "__main__":
    unittest.main()
