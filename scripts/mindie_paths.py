"""[Role] Canonical filesystem path authority for the DSH adapter (mirrors paths.cjs).
[Invariants] Must stay byte-for-byte consistent with paths.cjs across POSIX and Windows.
[Timeouts & Locks] One local adapter read per installed path; no subprocesses or locks.
[Side-Effect Policy] Read-only except ensure_private_dir(), which creates target directories.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import sys

PACKAGE_DIR = Path(__file__).resolve().parents[1]
APP_PARTS = ("mindie-agent", "dsh")
_WSL_MOUNT_RE = re.compile(r"^/mnt/[a-zA-Z](?:/|$)")
_NPM_SHIM_JS_RE = re.compile(r'"%(?:~dp0|dp0%)[\\/]?([^"\r\n]+\.(?:c?m?js))"', re.IGNORECASE)


def strip_win_extended_prefix(path_str: str) -> str:
    if os.name != "nt":
        return path_str
    if path_str.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path_str[8:]
    if path_str.startswith("\\\\?\\"):
        return path_str[4:]
    return path_str


def same_path(a: str | os.PathLike[str] | None, b: str | os.PathLike[str] | None) -> bool:
    if a is None or b is None:
        return a is b
    sa, sb = str(a), str(b)
    if not sa or not sb:
        return sa == sb
    try:
        if Path(sa).exists() and Path(sb).exists():
            return os.path.samefile(sa, sb)
    except TimeoutError:
        raise
    except OSError:
        pass
    try:
        ra = strip_win_extended_prefix(str(Path(sa).resolve()))
        rb = strip_win_extended_prefix(str(Path(sb).resolve()))
    except TimeoutError:
        raise
    except OSError:
        ra, rb = strip_win_extended_prefix(sa), strip_win_extended_prefix(sb)
    return os.path.normcase(os.path.normpath(ra)) == os.path.normcase(os.path.normpath(rb))


def _is_wsl() -> bool:
    return sys.platform == "linux" and (
        "WSL_DISTRO_NAME" in os.environ or Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
    )


def _is_wsl_windows_shim(path_str: str) -> bool:
    lower = path_str.lower()
    if lower.endswith((".exe", ".cmd", ".bat", ".ps1", ".com")):
        return True
    if not _WSL_MOUNT_RE.match(path_str):
        return False
    if Path(f"{path_str}.cmd").is_file() or Path(f"{path_str}.exe").is_file():
        return True
    try:
        with Path(path_str).open("rb") as fh:
            head = fh.read(512)
    except TimeoutError:
        raise
    except OSError:
        return True
    if head.startswith(b"MZ"):
        return True
    if head.startswith(b"#!"):
        first_line = head.splitlines(True)[0]
        if b"\r" in first_line or b".exe" in head.lower() or b"basedir=" in head:
            return True
    return False


def _win32_tool_candidates(cmd: str) -> list[Path]:
    base = cmd.lower().removesuffix(".exe").removesuffix(".cmd")
    home = Path.home()
    local_app = Path(os.environ.get("LOCALAPPDATA") or (home / "AppData" / "Local"))
    app_data = Path(os.environ.get("APPDATA") or (home / "AppData" / "Roaming"))
    prog_files = Path(os.environ.get("ProgramFiles") or r"C:\Program Files")
    out: list[Path] = []
    if base == "uv":
        out.extend([home / ".local" / "bin" / "uv.exe", local_app / "Microsoft" / "WinGet" / "Links" / "uv.exe"])
        winget_pkgs = local_app / "Microsoft" / "WinGet" / "Packages"
        if winget_pkgs.is_dir():
            out.extend(sorted(winget_pkgs.glob("astral-sh.uv_*/uv.exe")))
    elif base == "git":
        out.extend([prog_files / "Git" / "cmd" / "git.exe", local_app / "Programs" / "Git" / "cmd" / "git.exe"])
    elif base == "gh":
        out.extend([prog_files / "GitHub CLI" / "gh.exe", local_app / "Programs" / "GitHub CLI" / "gh.exe"])
    elif base == "node":
        out.extend([prog_files / "nodejs" / "node.exe", local_app / "Programs" / "nodejs" / "node.exe"])
    elif base == "dsh":
        out.extend([app_data / "npm" / "dsh.cmd", local_app / "npm" / "dsh.cmd"])
    return out


def which_native(cmd: str, path: str | None = None) -> str | None:
    """Resolve `cmd` without picking Windows PE/CRLF shims on WSL2 or WindowsApps 0-byte stubs."""
    search_path = os.environ.get("PATH", "") if path is None else path
    if _is_wsl():
        for entry in search_path.split(os.pathsep):
            if not entry:
                continue
            found = shutil.which(cmd, path=entry)
            if found and not _is_wsl_windows_shim(found):
                return found
        return None
    found = shutil.which(cmd, path=search_path)
    if os.name == "nt" and found:
        try:
            if "windowsapps" in found.replace("/", "\\").lower() and Path(found).stat().st_size == 0:
                found = None
        except TimeoutError:
            raise
        except OSError:
            found = None
    if os.name == "nt" and found is None and path is None:
        for cand in _win32_tool_candidates(cmd):
            if cand.is_file():
                return str(cand)
    return found


def unwrap_cli_argv(raw: str | Path) -> list[str] | None:
    """Resolve a CLI path to a `CreateProcessW`-safe argv list without `cmd.exe` batch parsing."""
    p = Path(raw).resolve()
    if os.name != "nt":
        return [str(p)]
    suf = p.suffix.lower()
    if suf == "":
        for ext in (".exe", ".cmd", ".bat"):
            cand = p.with_suffix(ext)
            if cand.is_file():
                p = cand.resolve()
                suf = ext
                break
    if suf == ".exe":
        return [str(p)]
    if suf in {".cmd", ".bat"}:
        sibling_exe = p.with_suffix(".exe")
        if sibling_exe.is_file():
            return [str(sibling_exe.resolve())]
        try:
            shim_text = p.read_bytes()[:16384].decode("utf-8", errors="replace")
        except TimeoutError:
            raise
        except OSError:
            return None
        m = _NPM_SHIM_JS_RE.search(shim_text)
        if not m:
            return None
        js_entry = (p.parent / m.group(1).replace("\\", "/")).resolve()
        if not js_entry.is_file():
            return None
        sibling_node = p.parent / "node.exe"
        node_bin = str(sibling_node.resolve()) if sibling_node.is_file() else (which_native("node.exe") or which_native("node"))
        if not node_bin or not node_bin.lower().endswith(".exe"):
            return None
        return [str(Path(node_bin).resolve()), str(js_entry)]
    return None


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
    return f'uv run --quiet --no-project --python 3.11 "{setup_script()}"'


def ensure_private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path
