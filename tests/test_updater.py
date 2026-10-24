"""Release selection and artifact checks for the silent updater."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tarfile
import tempfile
import unittest
import subprocess
import os
import queue
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import updater  # noqa: E402


def release(tag, *, draft=False, prerelease=False, version=None):
    version = version or tag.lstrip("v")
    return {
        "tag_name": tag,
        "draft": draft,
        "prerelease": prerelease,
        "assets": [
            {
                "name": f"mindie-agent-dsh-{version}.tgz",
                "browser_download_url": f"https://example.invalid/{version}/asset",
            },
            {
                "name": "SHA256SUMS",
                "browser_download_url": f"https://example.invalid/{version}/sums",
            },
        ],
    }


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        native = patch("native_update.install")
        self.native_install = native.start()
        self.addCleanup(native.stop)
        for name, result in (("prepare", None),):
            context = patch("component_update." + name, return_value=result)
            setattr(self, "component_" + name, context.start())
            self.addCleanup(context.stop)

    def test_killed_check_releases_the_native_update_lock_immediately(self):
        from mindie_knowledge.loop.locks import unlock_file
        code = (
            "import sys;sys.path.insert(0,sys.argv[1]);import updater;"
            "fd=updater.acquire_lock();print('locked' if fd is not None else 'busy',flush=True);"
            "sys.stdin.readline()"
        )
        child = subprocess.Popen([sys.executable, '-I', '-c', code, str(ROOT / 'scripts')],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, env=dict(os.environ))
        output = queue.Queue()
        reader = threading.Thread(target=lambda: output.put(child.stdout.readline()))
        reader.start()
        try:
            self.assertEqual(output.get(timeout=5).strip(), 'locked')
            self.assertIsNone(updater.acquire_lock())
            child.kill()
            child.wait(timeout=3)
            descriptor = updater.acquire_lock()
            self.assertIsNotNone(descriptor)
            unlock_file(descriptor)
            os.close(descriptor)
        finally:
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=3)
            reader.join(timeout=1)

    def test_release_checks_never_start_the_knowledge_loop(self):
        from unittest import mock
        for candidate in (False, True):
            with self.subTest(candidate=candidate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                engine = root / "engine.json"
                engine.write_text(json.dumps(dict(domain="test", root=str(root / "corpus"))))
                with mock.patch.dict("os.environ", {
                    "MINDIE_DSH_DATA_HOME": str(root),
                    "MINDIE_DSH_ENGINE_CONFIG": str(engine),
                }), mock.patch.object(updater, "fetch_releases", return_value=[release("v0.1.2")] if candidate else []), \
                        mock.patch.object(updater, "setup_candidate"), \
                        mock.patch.object(updater, "run", side_effect=AssertionError("release check started knowledge")):
                    if candidate:
                        package = updater.update_dir() / "generations/v0.1.2/package"
                        package.mkdir(parents=True)
                        (package / "package.json").write_text(json.dumps(dict(version="0.1.2")))
                    result = updater.check("web", "0.1.1", "https://example.invalid")
                    self.assertEqual(result["state"], "applied" if candidate else "up-to-date")
                    self.assertFalse((root / "corpus").exists())

    def test_only_newer_formal_releases_are_selected(self):
        values = [
            release("v0.0.1"),
            release("v0.0.3", prerelease=True),
            release("v0.0.4", draft=True),
            release("v0.0.2"),
        ]
        selected = updater.choose(values, "0.0.1")
        self.assertIsNotNone(selected)
        self.assertEqual(selected[1], "v0.0.2")
        self.assertIsNone(updater.choose([release("v0.0.1")], "0.0.1"))
        self.assertIsNone(updater.choose([{"tag_name": "main"}], "0.0.1"))

    def test_sha256sums_must_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            asset = root / "asset.tgz"
            asset.write_bytes(b"payload")
            digest = hashlib.sha256(b"payload").hexdigest()
            sums = root / "SHA256SUMS"
            sums.write_text(f"{digest}  mindie-agent-dsh-0.0.1.tgz\n")
            self.assertEqual(updater.verify_asset(asset, sums), digest)
            sums.write_text("0" * 64 + "  mindie-agent-dsh-0.0.1.tgz\n")
            with self.assertRaises(updater.UpdateError):
                updater.verify_asset(asset, sums)

    def test_unsafe_archive_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "asset.tgz"
            with tarfile.open(archive, "w:gz") as stream:
                data = b"{}"
                info = tarfile.TarInfo("../package.json")
                info.size = len(data)
                stream.addfile(info, io.BytesIO(data))
            with self.assertRaises(updater.UpdateError):
                updater.extract(archive, root / "out")

    def test_corrupt_update_config_raises_update_error(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.dict("os.environ", {"MINDIE_DSH_CONFIG_HOME": str(root)}):
                self.assertEqual(updater.update_config()["enabled"], True)
                (root / "update.json").write_text("{corrupt", encoding="utf-8")
                with self.assertRaises(updater.UpdateError):
                    updater.update_config()

    def test_default_current_version_reads_active_generation_or_package_manifest(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with mock.patch.dict("os.environ", {"MINDIE_DSH_DATA_HOME": str(root)}):
                package_version = updater.json.loads((ROOT / "package.json").read_text(encoding="utf-8"))["version"]
                self.assertEqual(updater.default_current_version(), package_version)
                updater.write_current(root / "gen", "v0.0.9", None)
                self.assertEqual(updater.default_current_version(), "0.0.9")


if __name__ == "__main__":
    unittest.main()
