"""Damaged saved choices remain visible and are never erased by the adapter."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mindie_sidecar
from mindie_knowledge import consent_store


class SavedChoiceTests(unittest.TestCase):
    def test_configure_reports_damaged_settings_without_erasing_them(self):
        for damaged in ("consent", "community"):
            with self.subTest(damaged=damaged), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                consent = root / "mindie-consent.json"
                community = root / "mindie-community.json"
                consent_store.record_choice(str(consent), "disabled")
                damaged_path = consent if damaged == "consent" else community
                damaged_path.write_bytes(b'{"saved-policy": unfinished')
                before = consent.read_bytes()
                request = dict(id=1, op="configure", args=dict(choice="disabled", cwd=str(root)))
                output = io.StringIO()
                with patch.dict(os.environ, MINDIE_DSH_CONFIG_HOME=str(root)), \
                        patch.object(sys, "stdin", io.StringIO(json.dumps(request) + "\n")), \
                        patch.object(mindie_sidecar, "diagnostic", return_value=None), \
                        redirect_stdout(output):
                    mindie_sidecar.main()
                result = json.loads(output.getvalue())
                self.assertFalse(result["ok"])
                self.assertEqual(result["id"], request["id"])
                self.assertIn("error", result)
                self.assertEqual(damaged_path.read_bytes(), b'{"saved-policy": unfinished')
                self.assertEqual(consent.read_bytes(), before)

    def test_default_initialization_preserves_corrupt_consent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            consent = root / "mindie-consent.json"
            consent.write_bytes(b'{"saved-choice": unfinished')
            with patch.dict(os.environ, MINDIE_DSH_CONFIG_HOME=str(root)):
                with self.assertRaises(consent_store.ConsentError):
                    mindie_sidecar.ensure_default_sharing()
            self.assertEqual(consent.read_bytes(), b'{"saved-choice": unfinished')
            self.assertFalse((root / "mindie-community.json").exists())
