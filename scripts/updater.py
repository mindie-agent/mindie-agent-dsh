#!/usr/bin/env python3
"""[Role] Bounded background release updater for mindie-agent-dsh.
[Invariants] Selects only formal non-draft/non-prerelease GitHub Releases; verifies SHA256SUMS and provisions candidate before switching current.json.
[Timeouts & Locks] Single-instance check.lock; candidate setup receives 600s total plus 10s outer cleanup allowance. Commands drain owned children on timeout.
[Side-Effect Policy] Writes generation artifacts under data_home()/update and atomically updates status.json and current.json via os.replace.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402
import setup_process  # noqa: E402

SCHEMA = "mindie-dsh-update-status/1"
DEFAULT_API = "https://api.github.com/repos/mindie-agent/mindie-agent-dsh/releases"
INSTALL_TIMEOUT = 600
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")


class UpdateError(RuntimeError):
    pass


def update_dir() -> Path:
    return mp.data_home() / "update"


def status_path() -> Path:
    return update_dir() / "status.json"


def lock_path() -> Path:
    return update_dir() / "check.lock"


def current_path() -> Path:
    return update_dir() / "current.json"


def write_current(package: Path, tag: str, previous: dict | None) -> None:
    atomic_json(
        current_path(),
        {
            "schema": "mindie-dsh-generation/1",
            "generation": str(package),
            "tag": tag,
            "previous_generation": (previous or {}).get("generation"),
            "previous_tag": (previous or {}).get("tag"),
            "updated_at": time.time(),
        },
    )


def config_path() -> Path:
    return mp.config_home() / "update.json"


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return default


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def update_config() -> dict:
    if not config_path().is_file():
        value = {}
    else:
        try:
            value = json.loads(config_path().read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpdateError(
                f"update configuration is unreadable: {type(exc).__name__}"
            ) from exc
    if not isinstance(value, dict):
        value = {}
    enabled = value.get("enabled", True)
    interval = value.get("interval_minutes", value.get("interval_hours", 0) * 60 or 5)
    return {
        "enabled": enabled if type(enabled) is bool else True,
        "interval_minutes": interval if type(interval) is int and 1 <= interval <= 10080 else 5,
    }


def version_of(tag: str):
    match = SEMVER.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def parse_current(value: str):
    return version_of(value) or (0, 0, 0)


def fetch_releases(api: str) -> list:
    request = urllib.request.Request(
        api,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "mindie-agent-dsh-updater/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read(4 * 1024 * 1024 + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"release lookup failed: {type(exc).__name__}") from exc
    if len(raw) > 4 * 1024 * 1024:
        raise UpdateError("release lookup exceeds the limit")
    try:
        values = json.loads(raw)
    except ValueError as exc:
        raise UpdateError("release lookup returned invalid JSON") from exc
    if not isinstance(values, list):
        raise UpdateError("release lookup did not return a list")
    return values


def choose(releases: list, current: str):
    current_version = parse_current(current)
    best = None
    for release in releases:
        if not isinstance(release, dict) or release.get("draft") or release.get("prerelease"):
            continue
        tag = release.get("tag_name")
        version = version_of(tag) if isinstance(tag, str) else None
        if version is None or version <= current_version:
            continue
        assets = release.get("assets")
        if not isinstance(assets, list):
            continue
        by_name = {
            asset.get("name"): asset.get("browser_download_url")
            for asset in assets
            if isinstance(asset, dict) and isinstance(asset.get("name"), str)
        }
        asset = by_name.get(f"mindie-agent-dsh-{tag.lstrip('v')}.tgz") or next(
            (url for name, url in by_name.items() if name.endswith(".tgz")), None
        )
        sums = by_name.get("SHA256SUMS")
        if asset and sums and (best is None or version > best[0]):
            best = (version, tag, asset, sums)
    return best


def download(url: str, destination: Path, limit: int = 128 * 1024 * 1024) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "mindie-agent-dsh-updater/1"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise UpdateError("release asset exceeds the limit")
                stream.write(chunk)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"release asset download failed: {type(exc).__name__}") from exc


def verify_asset(asset: Path, sums: Path) -> str:
    digest = hashlib.sha256()
    with asset.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == actual:
            return actual
    raise UpdateError("release SHA256SUMS does not contain the asset digest")


def extract(asset: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(asset, "r:gz") as archive:
            for member in archive.getmembers():
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise UpdateError("release archive contains an unsafe path")
            archive.extractall(destination)
    except (OSError, tarfile.TarError) as exc:
        raise UpdateError(f"release archive could not be extracted: {type(exc).__name__}") from exc
    package = destination / "package"
    if not (package / "package.json").is_file():
        raise UpdateError("release archive has no package/package.json")
    return package


def run(command, timeout, cwd=None, env=None):
    try:
        completed = setup_process.run(
            command, cwd=str(cwd) if cwd else None, env=env,
            timeout=timeout,
        )
    except TimeoutError:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return completed.returncode, (completed.stderr or completed.stdout or "").strip()[-2000:]


def setup_candidate(package: Path, python: str, *, artifact=None, apply=True) -> None:
    updater = shutil.which("uv")
    if updater is None:
        raise UpdateError("uv is not available to provision the candidate")
    command = [
        artifact['python'] if artifact else sys.executable,
        str(package / "scripts" / "setup.py"), "--python", python,
        *os.environ.get("MINDIE_DSH_UPDATE_SETUP_ARGS", "").split(),
        "--total-timeout", str(INSTALL_TIMEOUT),
    ]
    if artifact:
        command += ['--component-manifest', str(Path(artifact['generation']) / 'components.json')]
    if not apply:
        command.append('--prepare-only')
    code, detail = run(command, timeout=INSTALL_TIMEOUT + 10, cwd=package)
    if code != 0:
        raise UpdateError("candidate setup failed: " + detail[-800:])


def check(profile: str, current: str, api: str, apply: bool = True) -> dict:
    update_dir().mkdir(parents=True, exist_ok=True)
    pending = read_json(status_path(), {}).get('native', {})
    if pending.get('stage') in {'installing', 'unknown'}:
        raise UpdateError('previous native plugin write is unconfirmed; inspect native plugin status before recovery')
    active_version = current
    native_version = None
    if mp.config_file().is_file():
        from native_update import current_version as registered_version
        native_version = registered_version(profile)
        if native_version is not None:
            current = native_version
    status = {
        "schema": SCHEMA, "state": "checking", "current": current, "latest": None,
        "active_version": active_version, "native_version": native_version,
        "profile": profile, "checked_at": time.time(), "needs_host_reload": False, "detail": "",
    }
    atomic_json(status_path(), status)
    candidate = choose(fetch_releases(api), current)
    if candidate is None:
        from component_update import prepare as component_prepare
        adapter = read_json(mp.config_file(), {})
        artifact = (component_prepare(adapter.get('package_dir') or mp.PACKAGE_DIR,
                                      adapter['python'], active=True)
                    if adapter.get('python') else None)
        if artifact:
            package = Path(artifact['source'])
            setup_candidate(package, artifact['python'], artifact=artifact, apply=apply)
            if apply:
                previous = read_json(current_path(), {})
                write_current(package, current, previous)
            status['components'] = dict(status='active' if apply else 'staged', runtime=artifact['components'])
        else:
            status['components'] = dict(status='up_to_date' if adapter.get('python') else 'unconfigured')
        status.update(state="up-to-date", checked_at=time.time())
        atomic_json(status_path(), status)
        return status
    _version_tuple, tag, asset_url, sums_url = candidate
    status["latest"] = tag
    generation = update_dir() / "generations" / tag
    package = generation / "package"
    if not (package / "package.json").is_file():
        generation.mkdir(parents=True, exist_ok=True)
        asset, sums = generation / "asset.tgz", generation / "SHA256SUMS"
        download(asset_url, asset)
        download(sums_url, sums)
        verify_asset(asset, sums)
        package = extract(asset, generation)
        try:
            manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise UpdateError("candidate package.json is invalid") from exc
        if manifest.get("version") != tag.lstrip("v"):
            raise UpdateError("candidate version does not match the release tag")
    previous = read_json(current_path(), {})
    from component_update import prepare as component_prepare
    adapter = read_json(mp.config_file(), {})
    effective = component_prepare(package, adapter.get('python') or sys.executable)
    if effective:
        package = Path(effective['source'])
    setup_candidate(package, sys.executable, artifact=effective, apply=False)
    if not apply:
        status.update(
            state="staged", checked_at=time.time(), previous=previous.get("generation"),
            generation=str(package), needs_host_reload=False, detail="candidate prepared; pointer not switched",
        )
        atomic_json(status_path(), status)
        return status
    from native_update import install as native_install
    native_install(package, profile, status, lambda value: atomic_json(status_path(), value))
    setup_candidate(package, sys.executable, artifact=effective, apply=True)
    write_current(package, tag, previous if isinstance(previous, dict) else None)
    status.update(
        state="applied", checked_at=time.time(), previous=previous.get("generation"),
        generation=str(package),
        detail="runtime binding and native dependency updated; the launcher switches idle tools, profile definitions refresh on reload",
    )
    atomic_json(status_path(), status)
    return status


def acquire_lock():
    from mindie_knowledge.loop.locks import lock_file
    update_dir().mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        lock_file(descriptor, exclusive=True)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def recover(profile):
    status = read_json(status_path(), {})
    if status.get('native', {}).get('stage') in {'installing', 'unknown'}:
        raise UpdateError('native plugin write is unconfirmed; inspect the native registry before rollback')
    current = read_json(current_path(), {})
    previous = current.get('previous_generation')
    if not previous:
        return dict(recovered=False, reason='no_previous_generation')
    package = Path(previous)
    from native_update import install as native_install
    native_install(package, profile, status, lambda value: atomic_json(status_path(), value))
    manifest = package.parent / 'components.json'
    artifact = None
    if manifest.is_file():
        artifact = dict(generation=str(package.parent), python=str(
            package.parent / 'runtime/.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
    setup_candidate(package, sys.executable, artifact=artifact)
    write_current(package, str(current.get('previous_tag') or ''), current)
    status.update(state='recovered', generation=str(package), checked_at=time.time())
    atomic_json(status_path(), status)
    return dict(recovered=True, generation=str(package), status=status)


def default_current_version() -> str:
    adapter = read_json(mp.config_file(), {})
    if adapter.get('package_dir') and adapter.get('version'):
        return adapter['version']
    current = read_json(current_path(), {})
    if isinstance(current, dict) and isinstance(current.get("tag"), str) and current["tag"]:
        return current["tag"].lstrip("v")
    try:
        pkg = json.loads((mp.PACKAGE_DIR / "package.json").read_text(encoding="utf-8"))
        if isinstance(pkg.get("version"), str) and pkg["version"]:
            return pkg["version"]
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    return "0.1.4"


def record_update_failure(error: Exception, args) -> int:
    result = {**read_json(status_path(), {}),
        "schema": SCHEMA, "state": "failed", "current": args.current_version,
        "checked_at": time.time(), "detail": f"{type(error).__name__}: {str(error)[:800]}",
    }
    if result.get('native', {}).get('stage') in {'installing', 'unknown'}:
        result['state'] = 'unknown'
    from dsh_diagnostics import failure

    result["diagnostic"] = failure("update", "update", error, category="update")
    atomic_json(status_path(), result)
    if not args.silent:
        print(json.dumps(result, ensure_ascii=False))
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "status", "recover", "enable", "disable"))
    parser.add_argument("--profile", default=os.environ.get("DSH_PROFILE", "web"))
    parser.add_argument("--current-version", default=None)
    parser.add_argument("--api", default=os.environ.get("MINDIE_DSH_UPDATE_API", DEFAULT_API))
    parser.add_argument("--silent", action="store_true")
    parser.add_argument("--stage-only", action="store_true")
    args = parser.parse_args()
    if args.command == "status":
        print(json.dumps(read_json(status_path(), {"schema": SCHEMA, "state": "never"}), ensure_ascii=False))
        return 0
    if args.command in ("enable", "disable"):
        config = update_config()
        config["enabled"] = args.command == "enable"
        atomic_json(config_path(), config)
        print(json.dumps(config))
        return 0
    if args.command != 'recover' and not update_config()["enabled"]:
        print(json.dumps({"state": "disabled"}))
        return 0
    descriptor = acquire_lock()
    if descriptor is None:
        if not args.silent:
            print(json.dumps({"state": "busy"}))
        return 0
    try:
        if args.command == 'recover':
            print(json.dumps(recover(args.profile)))
            return 0
        current_version = args.current_version or default_current_version()
        result = check(args.profile, current_version, args.api, apply=not args.stage_only)
        if not args.silent:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except TimeoutError as exc:
        return record_update_failure(exc, args)
    except Exception as exc:  # noqa: BLE001
        return record_update_failure(exc, args)
    finally:
        from mindie_knowledge.loop.locks import unlock_file
        try:
            unlock_file(descriptor)
        finally:
            os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
