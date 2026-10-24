"""[Role] Bind DSH faults and reporter startup to the profile contribution authority.
[Invariants] Uses shared diagnostics only; never records or upgrades user consent.
[Timeouts & Locks] Recording is local; activation delegates a 75s startup budget.
[Side-Effect Policy] Writes shared diagnostic events and starts authorized workers.
"""

from __future__ import annotations

import argparse
import json
import os

import mindie_paths as mp


def bind_environment() -> None:
    os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(mp.diagnostics_config())


def effective_reporting(view: dict, saved: dict) -> dict:
    """The authority gates status too; a legacy choice cannot widen consent."""
    if saved.get("state") in {"corrupt", "unreadable"}:
        return dict(view, enabled=False, status="unavailable", consent=saved)
    if not saved.get("choice_revision"):
        return dict(view, enabled=False, status="needs_choice", consent=saved,
                    detail="The saved choice does not authorize sanitized fault reporting.")
    if saved.get("choice") != "contribute":
        return dict(view, enabled=False, status="disabled", consent=saved)
    return dict(view, consent=saved)


def status() -> dict:
    from mindie_diagnostics.integration import reporting_status
    from mindie_knowledge.consent_store import read

    result = reporting_status(config=mp.diagnostics_config())
    return effective_reporting(result, read(mp.config_home() / "mindie-consent.json"))


def activate(*, ensure=True) -> dict:
    from mindie_knowledge.consent_store import activate_reporting

    bind_environment()
    return activate_reporting(
        mp.config_home() / "mindie-consent.json",
        config=mp.diagnostics_config(), ensure=ensure,
    )


def failure(operation, stage, error, *, reportable=True, category=None) -> dict:
    try:
        from mindie_diagnostics.integration import record_failure

        bind_environment()
        receipt = record_failure(
            "mindie-agent-dsh", operation, stage=stage,
            category=category or ("internal" if reportable else "caller"),
            exception=error, host="dsh", version="0.1.1", reportable=reportable,
        )
        return {key: receipt[key] for key in ("incident_id", "record_ref", "logging_failed") if key in receipt}
    except TimeoutError:
        raise
    except Exception:
        return {"logging_failed": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("activate", "record"))
    parser.add_argument("--operation", default="setup")
    parser.add_argument("--category", default="internal")
    args = parser.parse_args()
    if args.command == "activate":
        result = activate()
    else:
        result = failure(args.operation, args.operation, None, category=args.category)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
