"""[Role] Prepare component-only generations for DSH's existing installer.
[Invariants] The active native package is retained with an independently locked
runtime. New plugin releases inherit all installed component version floors.
A missing source manifest or configured interpreter fails with FileNotFoundError
naming that artifact before catalog discovery or a build. Child failures carry
only typed public facts and a diagnostic reference, never exception text.
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
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILURE_SCHEMA = "mindie-component-failure/1"
_CATEGORIES = {"temporary_network", "rate_limited", "certificate", "deadline", "internal"}
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def failure_facts(error):
    """Only typed public facts cross installer processes; never stderr text."""
    import http.client
    import ssl
    import urllib.error
    facts = dict(getattr(error, "mindie_component_failure", {"type": type(error).__name__}))
    code, category = getattr(error, "code", None), getattr(error, "category", None)
    if isinstance(error, urllib.error.HTTPError):
        code = "release_http_error"
        category = "rate_limited" if error.code == 429 else "temporary_network"
    elif isinstance(error, ssl.SSLCertVerificationError):
        category = "certificate"
    elif isinstance(error, TimeoutError):
        category = "deadline"
    elif (isinstance(error, (urllib.error.URLError, ConnectionError, ssl.SSLError, http.client.HTTPException))
          and not isinstance(error, (http.client.InvalidURL, http.client.ImproperConnectionState,
                                     http.client.NotConnected, http.client.UnimplementedFileMode))):
        category = "temporary_network"
    if isinstance(code, str) and _CODE.fullmatch(code):
        facts["code"] = code
    if isinstance(category, str) and category in _CATEGORIES:
        facts["category"] = category
    status = getattr(error, "status", None)
    if type(status) is int and 100 <= status <= 599:
        facts["status"] = status
    return facts


def failure_envelope(error):
    value = dict(schema=FAILURE_SCHEMA, error=failure_facts(error))
    receipt = _diagnostic_reference(getattr(error, "mindie_diagnostic", None))
    if receipt is not None:
        value["diagnostic"] = receipt
    return value


def _diagnostic_reference(value):
    if not isinstance(value, dict) or type(value.get("logging_failed")) is not bool:
        return None
    incident = value.get("incident_id")
    if isinstance(incident, str) and re.fullmatch(r"[0-9a-f]{32}", incident):
        return dict(incident_id=incident, logging_failed=value["logging_failed"])
    if value == {"logging_failed": True}:
        return value
    return None


def recover_failure(raw, target):
    """Recover the closed envelope; unrelated child crashes remain internal."""
    if not isinstance(raw, str) or len(raw) > 8192:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("schema") != FAILURE_SCHEMA:
        return None
    original = value.get("error")
    if not isinstance(original, dict):
        return None
    facts = {"type": original.get("type") if isinstance(original.get("type"), str) else "RuntimeError"}
    for key in ("code", "category", "status"):
        item = original.get(key)
        valid = (isinstance(item, str) and _CODE.fullmatch(item) if key == "code" else
                 isinstance(item, str) and item in _CATEGORIES if key == "category" else
                 type(item) is int and 100 <= item <= 599)
        if valid:
            facts[key] = item
            setattr(target, key, item)
    target.mindie_component_failure = facts
    diagnostic = _diagnostic_reference(value.get("diagnostic"))
    if diagnostic is not None:
        target.mindie_diagnostic = diagnostic
    return target


def _require_source(source, python) -> None:
    """The updater expects these files. A bare command is resolved by the child."""
    root = Path(source)
    if not (root / "pyproject.toml").is_file() and not (root / "runtime-requirements.txt").is_file():
        raise FileNotFoundError("adapter source pyproject.toml is unavailable")
    runtime = Path(python)
    if runtime.is_absolute() and not runtime.is_file():
        raise FileNotFoundError("configured runtime python is unavailable")


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

    adapter = json.loads(mp.config_file().read_text(encoding="utf-8")) if mp.config_file().exists() else {}
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
    _require_source(source, python)
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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("python")
    parser.add_argument("--ensure-bound", action="store_true")
    parser.add_argument("--timeout", type=float, default=570)
    args = parser.parse_args()
    try:
        result = prepare(args.source, args.python, ensure_bound=args.ensure_bound, timeout=args.timeout)
    except Exception as error:
        print(json.dumps(failure_envelope(error), sort_keys=True))
        raise SystemExit(1)
    print(json.dumps(result, sort_keys=True))
