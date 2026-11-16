"""[Role] Prepare component-only generations for DSH's existing installer.
[Invariants] The active native package is retained with an independently locked
runtime. New plugin releases inherit all installed component version floors.
A missing source manifest or configured interpreter fails with FileNotFoundError
naming that artifact before catalog discovery or a build. Child failures carry
only typed public facts and a diagnostic reference, never exception text;
transport, DNS resolver, busy and deferred categories stay non-reportable
local/connectivity facts instead of becoming internal product bugs.
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
_CATEGORIES = {
    "authentication", "busy", "certificate", "deadline", "deferred",
    "internal", "permission", "prerequisite", "rate_limited", "resolver",
    "temporary_network", "validation",
}
# Categories the shared update policy does not know: keep them non-reportable
# and give each one a structured reason code instead of "internal".
_NON_REPORTABLE = {
    "prerequisite": ("prerequisite", "update_prerequisite_unavailable"),
    "resolver": ("connectivity", "update_transport_unavailable"),
    "busy": ("prerequisite", "update_deferred"),
    "deferred": ("prerequisite", "update_deferred"),
}
_OUTPUT_CATEGORIES = (
    ("resolver", ("temporary failure in name resolution",
                  "could not resolve host", "name or service not known",
                  "getaddrinfo failed", "version solving failed",
                  "conflicting dependencies",
                  "no solution found when resolving dependencies",
                  "requirements contain conflicting urls")),
    ("rate_limited", ("too many requests", "rate limit", "http 429")),
    ("certificate", ("certificate verify failed", "ssl certificate problem",
                     "unknown ca", "certificate has expired")),
    ("temporary_network", ("connection timed out", "read timed out",
                           "connection reset by peer", "connection refused",
                           "network is unreachable", "failed to connect")),
    ("deferred", ("operation_busy", "installation_busy", "check_busy",
                  '"status": "deferred"', '"status":"deferred"')),
)
_OUTPUT_CODES = {
    "resolver": "update_transport_unavailable",
    "rate_limited": "update_transport_unavailable",
    "certificate": "update_credentials_required",
    "temporary_network": "update_transport_unavailable",
    "deferred": "update_deferred",
}
_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def failure_facts(error):
    """Only typed public facts cross installer processes; never stderr text."""
    import http.client
    import socket
    import ssl
    import urllib.error
    facts = dict(getattr(error, "mindie_component_failure", {"type": type(error).__name__}))
    code, category = getattr(error, "code", None), getattr(error, "category", None)
    if isinstance(error, urllib.error.HTTPError):
        code = "release_http_error"
        category = ("rate_limited" if error.code == 429 else
                    "authentication" if error.code == 401 else
                    "permission" if error.code == 403 else
                    "temporary_network")
    elif isinstance(error, ssl.SSLCertVerificationError):
        category = "certificate"
    elif isinstance(error, TimeoutError):
        category = "deadline"
    elif isinstance(error, (socket.gaierror, socket.herror)):
        category = "resolver"
    elif isinstance(error, BlockingIOError):
        category = "busy"
    elif isinstance(error, urllib.error.URLError):
        reason = getattr(error, "reason", None)
        category = ("certificate" if isinstance(reason, ssl.SSLCertVerificationError) else
                    "resolver" if isinstance(reason, (socket.gaierror, socket.herror)) else
                    "temporary_network")
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


def _failure_chain(error):
    """Typed causes/contexts only, bounded; exception text is never parsed."""
    seen: set[int] = set()
    pending = [error]
    while pending and len(seen) < 32:
        current = pending.pop(0)
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        cause = getattr(current, "__cause__", None)
        if cause is not None:
            pending.append(cause)
        if not getattr(current, "__suppress_context__", False):
            context = getattr(current, "__context__", None)
            if context is not None:
                pending.append(context)


def update_failure_classification(error):
    """Map an owned updater category the shared policy would call internal.

    Returns `(category, reportable, reason_code)` or `None` so the shared
    `update_failure_policy` still classifies its own transport/credential
    vocabulary. Typed DNS and lock facts are handled here because a recovered
    child failure loses its concrete exception type.
    """
    import socket
    for current in _failure_chain(error):
        category = getattr(current, "category", None)
        if isinstance(category, str) and category in _NON_REPORTABLE:
            mapped, code = _NON_REPORTABLE[category]
            return mapped, False, code
        if isinstance(current, (socket.gaierror, socket.herror)):
            return "connectivity", False, "update_transport_unavailable"
        if isinstance(current, BlockingIOError):
            return "prerequisite", False, "update_deferred"
    return None


def classify_update_output(text):
    """Narrow category for an untyped child diagnostic; never echoes output."""
    if not isinstance(text, str) or not text.strip():
        return None
    sample = text[-16384:].casefold()
    for category, phrases in _OUTPUT_CATEGORIES:
        if any(phrase in sample for phrase in phrases):
            return category, _OUTPUT_CODES[category]
    return None


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
