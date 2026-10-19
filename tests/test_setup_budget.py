"""The updater's shared budget limits actual setup child processes."""
import importlib.util
import sys
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
