"""Offline contract tests for the DSH bundle and release pins."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "package.json"
PATCH = ROOT / "cordis.patch.yml"
SKILL = ROOT / "skills" / "mindie-agent" / "SKILL.md"

STANDARD_ONLY = {
    "@deepseek-ai/dsh-skill-filesystem",
    "@deepseek-ai/dsh-mcp-client",
}
LOCAL_PLUGIN = "./host.mjs"

EXPECTED_COMPONENTS = {
    "mindie-knowledge": (
        "0.11.0",
        "81fb4e222f0cba5cffe50824ffd55b33d312283d",
    ),
    "remote-dev": (
        "0.9.5",
        "46a91a5b409506760ea530f7037a3f9f04d9a2d7",
    ),
    "mindie-diagnostics": (
        "0.4.1",
        "10cc3391ed358d7b7e0e1acb99ca5be4d1934b47",
    ),
}


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
        self.assertEqual(package["version"], "0.0.1")
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

    def test_patch_resolves_its_own_package_from_the_profile(self) -> None:
        text = PATCH.read_text(encoding="utf-8")
        self.assertNotIn("@deepseek-ai/dsh-experimental-", text)
        self.assertIn("createRequire(baseUrl)('mindie-agent-dsh/paths.cjs')", text)
        # Host rows must not depend on a preset mount.
        self.assertNotIn("preset-", text)

    def test_skill_frontmatter_and_size(self) -> None:
        raw = SKILL.read_text(encoding="utf-8")
        self.assertLess(len(raw.encode("utf-8")), 8192, "skill must fit the pruner threshold")
        self.assertTrue(raw.startswith("---\n"))
        frontmatter = raw.split("---\n", 2)[1]
        meta = yaml.safe_load(frontmatter)
        self.assertEqual(meta["name"], "mindie-agent")
        self.assertTrue(meta["description"].strip())
        self.assertIs(meta["disable-model-invocation"], True)
        for reference in ("knowledge.md", "remote-dev.md", "limitations.md"):
            self.assertTrue(
                (ROOT / "skills" / "mindie-agent" / "references" / reference).is_file(),
                reference,
            )

    def test_release_pins_are_consistent(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "runtime-requirements.txt").read_text(encoding="utf-8")
        lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
        compatibility = json.loads((ROOT / "harness-compatibility.json").read_text(encoding="utf-8"))
        self.assertEqual(compatibility["plugin_version"], "0.0.1")
        self.assertEqual(compatibility["verified"][0]["dsh_version"], "0.2.0-rc.2")
        direct = {"mindie-knowledge", "remote-dev"}
        for name, (version, commit) in EXPECTED_COMPONENTS.items():
            if name in direct:
                self.assertIn(commit, pyproject, f"{name} commit missing from pyproject")
                self.assertIn(commit, requirements, f"{name} commit missing from requirements")
            self.assertIn(commit, lock, f"{name} commit missing from uv.lock")
            self.assertRegex(
                lock,
                rf'name = "{re.escape(name)}"\nversion = "{re.escape(version)}"',
                f"{name} version/hash mismatch in uv.lock",
            )
        for line in requirements.splitlines():
            if line and not line.startswith("#"):
                self.assertIn(line.strip(), pyproject, "requirements/pyproject drift")


if __name__ == "__main__":
    unittest.main()
