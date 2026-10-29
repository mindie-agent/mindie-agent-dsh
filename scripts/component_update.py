"""[Role] Prepare component-only generations for DSH's existing installer.
[Invariants] The active native package is retained with an independently locked
runtime. New plugin releases inherit all installed component version floors.
[Timeouts & Locks] Metadata, catalog and build share one deadline; the builder
has at most 420s and leaves five seconds for cleanup. The setup caller reserves
180s shared across its remaining probe/configuration/handoff stages.
[Side-Effect Policy] Builds software and invokes setup's idle switch only;
no knowledge session, feed sync or model work is performed.
"""
from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))


def prepare(source, python, *, force=False, active=False, ensure_bound=False, timeout=570):
    import mindie_paths as mp
    from mindie_knowledge.component_bindings import required_components, rebind_commands
    from mindie_knowledge.component_releases import Catalog
    from mindie_knowledge.component_runtime import source_baseline, discover, build
    from semantic_version import Version

    deadline = time.monotonic() + timeout
    def remaining(reserve=0):
        available = deadline - time.monotonic() - reserve
        if available <= 0:
            raise TimeoutError("component preparation exceeded its total budget")
        return available

    adapter = json.loads(mp.config_file().read_text()) if mp.config_file().exists() else {}
    required = required_components(adapter, timeout=min(15, remaining()))
    if ensure_bound:
        missing = False
        for name, floor in required.items():
            try:
                missing |= Version(metadata.version(name)) < Version(floor)
            except metadata.PackageNotFoundError:
                missing = True
        if not missing:
            return None
    baseline = source_baseline(source)
    selected = discover(python, baseline["runtime"], force=force,
                        required_components=required,
                        catalog=Catalog(budget=min(120, remaining(20))))
    needs_binding = bool(adapter.get("component_commands")) and (
        rebind_commands(adapter, python, selected["runtime"]) != adapter["component_commands"])
    if active and not selected["changed"] and not needs_binding:
        return None
    if (not needs_binding
            and {name: row["revision"] for name, row in selected["runtime"].items()} == baseline["runtime"]):
        return None
    return build(source, mp.data_home() / "update/component-runtimes", baseline,
                 selected["runtime"], python=python, timeout=min(420, remaining(5)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("python")
    parser.add_argument("--ensure-bound", action="store_true")
    parser.add_argument("--timeout", type=float, default=570)
    args = parser.parse_args()
    print(json.dumps(prepare(args.source, args.python, ensure_bound=args.ensure_bound,
                             timeout=args.timeout), sort_keys=True))
