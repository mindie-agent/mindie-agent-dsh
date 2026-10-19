"""Offline contract tests for the DSH bundle and release pins."""

from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package.json"
PATCH = ROOT / "cordis.patch.yml"

STANDARD_ONLY = {
    "@deepseek-ai/dsh-skill-filesystem",
    "@deepseek-ai/dsh-mcp-client",
}
LOCAL_PLUGIN = "./host.mjs"
GIT_PIN_RE = re.compile(
    r"^([a-z0-9-]+)\s*@\s*git\+https://github\.com/mindie-agent/[a-z0-9-]+@([0-9a-f]{40})$"
)


class PatchLoader(yaml.SafeLoader):
    """SafeLoader that keeps ``!!js`` expressions as literal scalars."""


PatchLoader.add_constructor(
    "tag:yaml.org,2002:js",
    lambda loader, node: loader.construct_scalar(node),
)


def load_patch() -> list[dict]:
    data = yaml.load(PATCH.read_text(encoding="utf-8"), Loader=PatchLoader)
    if not isinstance(data, list):
        raise AssertionError("patch must be a list")
    return data


class PackageContractTests(unittest.TestCase):
    def test_package_manifest(self) -> None:
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        self.assertEqual(package["name"], "mindie-agent-dsh")
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(package["version"], project["version"])
        self.assertEqual(package["dsh"]["bundle"]["patch"], "./cordis.patch.yml")
        self.assertEqual(package["peerDependencies"]["@deepseek-ai/dsh"], "0.2.0-rc.2")
        self.assertTrue(package["peerDependenciesMeta"]["@deepseek-ai/dsh"]["optional"])

    def test_patch_only_inserts_stable_packages(self) -> None:
        rows: list[dict] = []
        for layer in load_patch():
            self.assertIn("insert", layer)
            self.assertEqual(set(layer), {"insert"})
            rows.extend(layer["insert"])
        ids = [row.get("id") for row in rows]
        self.assertEqual(len(ids), len(set(ids)), "row ids must be unique")
        self.assertEqual(
            set(ids),
            {
                "mindie-agent-skill",
                "mindie-agent-host",
                "mindie-agent-remote-mcp",
            },
        )
        names = {row["name"] for row in rows}
        self.assertEqual(names, STANDARD_ONLY | {LOCAL_PLUGIN})
        for row in rows:
            self.assertFalse(
                row["name"].startswith("@deepseek-ai/dsh-experimental-"),
                row["name"],
            )

    def test_release_pins_are_consistent(self) -> None:
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "runtime-requirements.txt").read_text(encoding="utf-8")
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        compatibility = json.loads((ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
        self.assertEqual(compatibility["plugin_version"], package["version"])
        self.assertEqual(compatibility["verified"][0]["dsh_version"], "0.2.0-rc.2")
        req_lines = [
            line.strip()
            for line in requirements.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        self.assertTrue(req_lines)
        for line in req_lines:
            match = GIT_PIN_RE.match(line)
            self.assertIsNotNone(match, f"invalid git pin format: {line}")
            name, commit = match.groups()
            self.assertIn(line, pyproject, "requirements/pyproject drift")
            self.assertIn(commit, lock, f"{name} commit missing from uv.lock")


if __name__ == "__main__":
    unittest.main()
