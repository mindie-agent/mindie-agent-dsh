"""[Role] Run an explicit knowledge feed refresh through the installed CLI.
[Invariants] Preserve each feed receipt and distinguish completed, deferred and failed refreshes.
[Timeouts & Locks] 120s execution plus 5s owned-process cleanup; callers budget admission separately.
[Side-Effect Policy] Refreshes only the selected local feed cache; starts no knowledge service or publisher.
"""

from __future__ import annotations

import json

from component_tools import run


def sync_feeds(python, engine):
    execution = run(
        [python, "-I", "-m", "mindie_knowledge.loop.cli", "sync",
         "--config", str(engine), "--resume"],
        timeout=120.0, max_output=1024 * 1024, cancel=None,
    )
    if execution["status"] != "completed" or execution["exit_code"] != 0 or execution["output_truncated"]:
        return {"status": "sync_failed", "execution": execution, "_tool_error": True}
    rows = json.loads(execution["stdout"])
    good = {"synced", "unchanged"}
    pending = {"busy", "deferred"}
    failed = any(
        row["status"] not in good | pending
        or row.get("cleanup_status") == "failed"
        or row.get("receipt_status") == "failed"
        for row in rows
    )
    incomplete = failed or any(row["status"] in pending for row in rows)
    committed = any(row["status"] in good | {"partial"} for row in rows)
    status = ("ok" if not incomplete else "degraded" if committed
              else "sync_failed" if failed else "deferred")
    result = {"status": status, "feeds": rows, "_tool_error": status != "ok"}
    if execution.get("cleanup_error"):
        result["cleanup_warning"] = execution["cleanup_error"]
    return result
