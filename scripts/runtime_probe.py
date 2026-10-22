#!/usr/bin/env python3
"""[Role] Standalone runtime probe for the pinned MindIE Agent provisioner.

[Invariants]
- Verifies SQLite FTS5 plus the pinned component imports before publication.
- Emits exactly one JSON object on stdout; failures exit nonzero.

[Timeouts & Locks]
- In-memory SQLite only; the caller owns the subprocess timeout.

[Side-Effect Policy]
- Read-only imports and an in-memory database; no files, network, or state.
"""

from __future__ import annotations

import importlib.metadata as md
import json
import sqlite3
import sys

import mindie_knowledge  # noqa: F401  (presence check)
import remote_dev  # noqa: F401  (presence check)

_COMPONENTS = ("mindie-knowledge", "remote-dev", "mindie-diagnostics")


def _version(name: str) -> str | None:
    try:
        return md.version(name)
    except (md.PackageNotFoundError, ValueError):
        return None


def main() -> int:
    database = sqlite3.connect(":memory:")
    try:
        database.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
    finally:
        database.close()

    json.dump(
        {
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "executable": sys.executable,
            "versions": {name: _version(name) for name in _COMPONENTS},
        },
        sys.stdout,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
