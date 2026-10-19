"""The updater's shared budget limits actual setup child processes."""
import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup.py"
spec = importlib.util.spec_from_file_location("dsh_setup_budget", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SetupBudgetTests(unittest.TestCase):
    def tearDown(self):
        setup.RUN_DEADLINE = None

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
