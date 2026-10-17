"""MindIE-owned paths for the DSH adapter.

These functions mirror ``paths.cjs`` exactly; ``tests/test_paths.py`` compares
the two so the bundle patch and the Python runtime cannot drift.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parents[1]
APP_PARTS = ("mindie-agent", "dsh")


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def data_home() -> Path:
    override = _env("MINDIE_DSH_DATA_HOME")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt":
        base = _env("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = _env("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base).expanduser() / APP_PARTS[0] / APP_PARTS[1]


def config_home() -> Path:
    override = _env("MINDIE_DSH_CONFIG_HOME")
    if override:
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
    return Path(override).expanduser().resolve() if override else config_home() / "engine.json"


def state_dir() -> Path:
    override = _env("MINDIE_DSH_STATE_DIR")
    return Path(override).expanduser().resolve() if override else data_home() / "state"


def venv_dir() -> Path:
    return data_home() / "venv"


def venv_python() -> Path:
    if os.name == "nt":
        return venv_dir() / "Scripts" / "python.exe"
    return venv_dir() / "bin" / "python"


def skills_dir() -> Path:
    return PACKAGE_DIR / "skills"


def knowledge_script() -> Path:
    return PACKAGE_DIR / "scripts" / "knowledge_mcp.py"


def setup_script() -> Path:
    return PACKAGE_DIR / "scripts" / "setup.py"


def setup_command() -> str:
    return (
        "uv run --quiet --no-project --python 3.11 "
        + str(setup_script())
    )


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path
