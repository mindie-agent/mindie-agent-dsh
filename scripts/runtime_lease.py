#!/usr/bin/env python3
"""[Role] Hold one process-lifetime shared runtime lease for resident DSH
services (`knowledge_mcp`, `component_mcp`, and `mindie_sidecar`).
[Invariants] Acquires both `mindie_knowledge.storage.acquire_runtime_lease` and
`mindie_harness.locks.acquire_generation_lease` on disposable runtime generations;
development interpreters outside managed generations yield no lease.
[Timeouts & Locks] Non-blocking shared lease probes; never retries or blocks startup.
[Side-Effect Policy] Creates/retains only `.inuse.lock` / generation lease descriptors
inside an existing runtime generation until OS process exit.
"""
from __future__ import annotations

from pathlib import Path
import sys

_HELD: list = []


def hold_runtime_lease(executable: str | None = None):
    """Acquire and retain shared generation leases until process exit."""
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
    try:
        from mindie_harness.locks import acquire_generation_lease

        harness_lease = acquire_generation_lease(Path(directory))
        harness_lease.__enter__()
        _HELD.append(harness_lease)
    except TimeoutError:
        raise
    except Exception:  # noqa: BLE001
        pass
    return lease


def held_leases() -> tuple:
    return tuple(_HELD)


__all__ = ["held_leases", "hold_runtime_lease"]
