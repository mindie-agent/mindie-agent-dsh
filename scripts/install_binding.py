"""[Role] Publish one complete DSH runtime binding after idle service handoff.
[Invariants] Candidate engine/config paths are immutable. A changed current
binding defers the update; failed publication restores exact old retirements.
[Timeouts & Locks] One setup lock and retirement/restoration share the caller's
control budget. The bootstrap subprocess reserves five seconds for cleanup.
[Side-Effect Policy] Stops only idle knowledge services and atomically replaces
the native adapter JSON. Never changes consent or invokes models/publication.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import time

from mindie_knowledge.loop.domain_catalog import retire_changed, restore_changed
from mindie_knowledge.loop.locks import StartLock
from mindie_knowledge.markdown import _atomic_write_text


def activate(path, previous, candidate, *, timeout=30):
    path = Path(path)
    deadline = time.monotonic() + timeout
    lock = StartLock(path.with_name(path.name + '.setup.lock'))
    lock.acquire(wait=max(0, deadline - time.monotonic()))
    try:
        current = json.loads(path.read_text()) if path.exists() else {}
        if current != previous:
            return dict(status='deferred', reason='installation_changed')
        report = retire_changed(previous, candidate, timeout=max(0, deadline - time.monotonic()), include_default=True)
        if report['status'] != 'retired':
            return dict(status=report['status'], domains=report)
        try:
            _atomic_write_text(path, json.dumps(candidate, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
        except TimeoutError:
            raise
        except (OSError, ValueError) as exc:
            restoration = restore_changed(report, timeout=max(0, deadline - time.monotonic()))
            return dict(status='incomplete', error_type=type(exc).__name__, restoration=restoration)
        return dict(status='active', domains=report)
    finally:
        lock.release()


if __name__ == '__main__':
    print(json.dumps(activate(**json.load(sys.stdin)), ensure_ascii=False))
