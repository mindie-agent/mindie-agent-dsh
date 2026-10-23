"""Host-environment isolation for the MindIE Agent test suite.

[Role]
Provides the module-level diagnostic fallback paths and an autouse fixture that
gives every test a private data/config/state/XDG surface.

[Invariants]
- No test writes to the real ``~/.local`` or ``~/.config`` trees.
- Diagnostics roots/configs point inside the per-test ``tmp_path``.
- Failure recorders opened by a test are closed before the next test.

[Timeouts & Locks]
- Fixture setup is in-memory path creation only; no locks are acquired.

[Side-Effect Policy]
- Creates temporary directories, mutates process environment only through
  ``monkeypatch``, and closes test-owned diagnostics recorders on teardown.
"""

import atexit
import os
from pathlib import Path
import shutil
import tempfile

import pytest

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
    "MINDIE_DSH_DATA_HOME": ("data",),
    "MINDIE_DSH_CONFIG_HOME": ("config",),
    "MINDIE_DSH_STATE_DIR": ("state",),
    "MINDIE_DIAGNOSTICS_ROOT": ("diagnostics", "logs"),
    "MINDIE_DIAGNOSTICS_CONFIG": ("diagnostics", "diagnostics.json"),
    "XDG_CONFIG_HOME": ("xdg-config",),
    "XDG_DATA_HOME": ("xdg-data",),
}


def _close_failure_recorders() -> None:
    try:
        from mindie_diagnostics import logging
    except ImportError:  # pragma: no cover - diagnostics is a required component
        return
    for key, recorder in list(logging._FAILURE_RECORDERS.items()):
        recorder.close()
        logging._FAILURE_RECORDERS.pop(key, None)


@pytest.fixture(autouse=True)
def isolated_host_environment(tmp_path, monkeypatch):
    """Isolate every host-owned path for one test and clean up recorders."""
    for variable, parts in _ISOLATED_PATHS.items():
        location = tmp_path.joinpath(*parts)
        if location.suffix:
            location.parent.mkdir(parents=True, exist_ok=True)
        else:
            location.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv(variable, str(location))
    real_ssh = _REAL_HOME / ".ssh"
    home_ssh = tmp_path / "home" / ".ssh"
    if real_ssh.is_dir() and not home_ssh.exists():
        home_ssh.symlink_to(real_ssh, target_is_directory=True)
    yield
    _close_failure_recorders()
