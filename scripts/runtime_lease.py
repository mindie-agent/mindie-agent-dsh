#!/usr/bin/env python3
"""[Role] Hold one process-lifetime shared runtime lease for resident DSH
services (knowledge MCP, component MCP, and the warm sidecar).
[Invariants] Only a directory recognized by
`mindie_knowledge.storage.owning_dir(sys.executable)` is leased; a development
interpreter outside a disposable generation yields no lease. Acquisition is
non-blocking and never retries. The OS releases the lock on process exit, so a
resident service does not grow a release/cleanup path of its own.
[Timeouts & Locks] One non-blocking shared lease probe; a busy or vanished
generation is reported as unavailable instead of waiting.
[Side-Effect Policy] Creates/removes only the shared `.inuse.lock` files inside
an existing runtime generation; no config, state, network, or model I/O.
"""
from __future__ import annotations

import sys

_HELD: list = []


def hold_runtime_lease(executable: str | None = None):
    """Acquire and retain a shared generation lease until process exit."""
    try:
        from mindie_knowledge.storage import acquire_runtime_lease, owning_dir
    except TimeoutError:
        raise
    except ImportError:
        return None
    directory = owning_dir(str(executable or sys.executable))
    if directory is None:
        return None
    try:
        lease = acquire_runtime_lease(directory)
    except TimeoutError:
        raise
    except (OSError, RuntimeError):
        return None
    _HELD.append(lease)
    return lease


def held_leases() -> tuple:
    return tuple(_HELD)


__all__ = ["held_leases", "hold_runtime_lease"]
