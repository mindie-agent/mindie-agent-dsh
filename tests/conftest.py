"""Host-environment isolation for the MindIE Agent test suite.

[Role]
Provides the module-level diagnostic fallback paths and an autouse fixture that
gives every test a private data/config/state/XDG/Windows AppData surface.

[Invariants]
- No test writes to real user directories (`~/.local`, `~/.config`, `%APPDATA%`, `%LOCALAPPDATA%`).
- Diagnostics roots/configs point inside the per-test ``tmp_path``.
- Failure recorders opened by a test are closed before teardown removes directories.

[Timeouts & Locks]
- Fixture setup is in-memory path creation only; no locks are acquired.

[Side-Effect Policy]
- Creates temporary directories, mutates process environment only through
  ``monkeypatch``, and closes test-owned diagnostics recorders on teardown.
"""

from __future__ import annotations

import atexit
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

import pytest


def _force_writable(func, target, _exc):
    try:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        func(target)
    except TimeoutError:
        raise
    except OSError:
        pass


def rmtree_clean(target: Path) -> None:
    if not target.exists():
        return
    resolved = target.resolve()
    raw = str(resolved)
    rm_target: str | Path = (
        (raw if raw.startswith("\\\\?\\") else f"\\\\?\\{raw}")
        if os.name == "nt"
        else resolved
    )
    if sys.version_info >= (3, 12):
        shutil.rmtree(rm_target, onexc=_force_writable)
    else:
        shutil.rmtree(rm_target, onerror=_force_writable)


_REAL_HOME = Path.home()
_DIAG_ROOT = Path(tempfile.mkdtemp(prefix="mindie-dsh-test-diag-")).resolve()
_DEFAULT_DIAGNOSTICS_ROOT = _DIAG_ROOT / "logs"
_DEFAULT_DIAGNOSTICS_CONFIG = _DIAG_ROOT / "diagnostics.json"
atexit.register(lambda: shutil.rmtree(_DIAG_ROOT, ignore_errors=True))
os.environ["MINDIE_DIAGNOSTICS_ROOT"] = str(_DEFAULT_DIAGNOSTICS_ROOT)
os.environ["MINDIE_DIAGNOSTICS_CONFIG"] = str(_DEFAULT_DIAGNOSTICS_CONFIG)

_ISOLATED_PATHS = {
    "HOME": ("home",),
    "USERPROFILE": ("home",),
    "APPDATA": ("appdata", "Roaming"),
    "LOCALAPPDATA": ("appdata", "Local"),
    "MINDIE_CONFIG_HOME": ("mindie-config",),
    "MINDIE_DATA_HOME": ("mindie-data",),
    "MINDIE_DSH_DATA_HOME": ("data",),
    "MINDIE_DSH_CONFIG_HOME": ("config",),
    "MINDIE_DSH_STATE_DIR": ("state",),
    "MINDIE_DIAGNOSTICS_ROOT": ("diagnostics", "logs"),
    "MINDIE_DIAGNOSTICS_CONFIG": ("diagnostics", "diagnostics.json"),
    "REMOTE_DEV_AUTH_DIR": ("remote-auth",),
    "XDG_CONFIG_HOME": ("xdg-config",),
    "XDG_DATA_HOME": ("xdg-data",),
    "XDG_STATE_HOME": ("xdg-state",),
    "XDG_CACHE_HOME": ("xdg-cache",),
}


def close_failure_recorders() -> None:
    try:
        from mindie_diagnostics import logging
    except ImportError:  # pragma: no cover - diagnostics is a required component
        return
    for key, recorder in list(logging._FAILURE_RECORDERS.items()):
        recorder.close()
        logging._FAILURE_RECORDERS.pop(key, None)


_close_failure_recorders = close_failure_recorders


@pytest.fixture(autouse=True)
def isolated_host_environment(request, tmp_path, monkeypatch):
    """Isolate every host-owned path for one test and clean up recorders."""
    for variable, parts in _ISOLATED_PATHS.items():
        location = tmp_path.joinpath(*parts)
        if location.suffix:
            location.parent.mkdir(parents=True, exist_ok=True)
        else:
            location.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(location))
    if request.node.get_closest_marker("live_npu") is not None:
        real_ssh = _REAL_HOME / ".ssh"
        home_ssh = tmp_path / "home" / ".ssh"
        if real_ssh.is_dir() and not home_ssh.exists():
            try:
                home_ssh.symlink_to(real_ssh, target_is_directory=True)
            except TimeoutError:
                raise
            except OSError:
                shutil.copytree(real_ssh, home_ssh)
    yield
    close_failure_recorders()
