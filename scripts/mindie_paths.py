"""[Role] Canonical filesystem path authority for the DSH adapter (mirrors paths.cjs).
[Invariants] Must stay byte-for-byte consistent with paths.cjs across POSIX and Windows.
[Timeouts & Locks] One local adapter read per installed path; no subprocesses or locks.
[Side-Effect Policy] Read-only except ensure_private_dir(), which creates target directories.
"""

from __future__ import annotations

import os
import json
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
APP_PARTS = ("mindie-agent", "dsh")


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() or None if isinstance(value, str) else None


def data_home() -> Path:
    if override := _env("MINDIE_DSH_DATA_HOME"):
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = _env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = _env("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / APP_PARTS[0] / APP_PARTS[1]


def config_home() -> Path:
    if override := _env("MINDIE_DSH_CONFIG_HOME"):
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = _env("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = _env("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / APP_PARTS[0] / APP_PARTS[1]


def config_file() -> Path:
    override = _env("MINDIE_DSH_CONFIG")
    return Path(override).expanduser().resolve() if override else config_home() / "config.json"


def engine_config() -> Path:
    override = _env("MINDIE_DSH_ENGINE_CONFIG")
    if override:
        return Path(override).expanduser().resolve()
    return _installed_path('engine_config') or config_home() / 'engine.json'


def diagnostics_config() -> Path:
    return config_home() / "diagnostics.json"


def state_dir() -> Path:
    override = _env("MINDIE_DSH_STATE_DIR")
    return Path(override).expanduser().resolve() if override else data_home() / "state"


def venv_dir() -> Path:
    return _installed_path('venv_dir') or data_home() / "venv"


def venv_python() -> Path:
    return _installed_path('python') or venv_dir() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _installed_path(name: str) -> Path | None:
    """Resolve the one published adapter binding, preserving legacy bootstrap paths."""
    try:
        value = json.loads(config_file().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ValueError('DSH adapter configuration must be an object')
    path = value.get(name)
    if path is None:
        return None
    if not isinstance(path, str) or not Path(path).is_absolute():
        raise ValueError('DSH adapter ' + name + ' must be an absolute path')
    return Path(path)


def skills_dir() -> Path:
    return PACKAGE_DIR / "skills"


def knowledge_script() -> Path:
    return PACKAGE_DIR / "scripts" / "knowledge_mcp.py"


def setup_script() -> Path:
    return PACKAGE_DIR / "scripts" / "setup.py"


def setup_command() -> str:
    return f"uv run --quiet --no-project --python 3.11 {setup_script()}"


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
