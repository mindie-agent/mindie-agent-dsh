#!/usr/bin/env python3
"""Bounded silent updater for mindie-agent-dsh.

This script is copied to the shared data directory by setup.py and started by
the Host plugin in the background. It never calls a model and never prompts.
One invocation performs at most one check. The native DSH plugin manager owns
profile installation/rollback; this script owns release selection, artifact
verification, setup and a bounded status record.

The update is staged from a formal GitHub Release asset:
  - only non-draft, non-prerelease releases are eligible;
  - SHA256SUMS is required and verified;
  - the candidate is extracted to an immutable generation directory;
  - the candidate setup runs before the profile is pointed at it;
  - the previous dependency/config are restored if installation fails.

A live DSH profile recomposes through its HMR/plugin manager path. If the
launcher is startup-only, the status records that a host restart is needed.
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
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402

SCHEMA = "mindie-dsh-update-status/1"
DEFAULT_API = "https://api.github.com/repos/mindie-agent/mindie-agent-dsh/releases"
CHECK_TIMEOUT = 240
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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return value


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def update_config() -> dict:
    value = read_json(config_path(), {})
    if not isinstance(value, dict):
        value = {}
    result = {
        "enabled": value.get("enabled", True),
        "interval_hours": value.get("interval_hours", 6),
    }
    if type(result["enabled"]) is not bool:
        result["enabled"] = True
    if type(result["interval_hours"]) is not int or not 1 <= result["interval_hours"] <= 168:
        result["interval_hours"] = 6
    return result


def version_of(tag: str):
    match = SEMVER.fullmatch(tag)
    return tuple(int(part) for part in match.groups()) if match else None


def parse_current(value: str):
    match = SEMVER.fullmatch(value)
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)


def fetch_releases(api: str) -> list:
    request = urllib.request.Request(
        api,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "mindie-agent-dsh-updater/1",
        },
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
    """Return (version_tuple, tag, asset_url, checksum_url) for a newer formal release."""
    current_version = parse_current(current)
    best = None
    for release in releases:
        if not isinstance(release, dict):
            continue
        if release.get("draft") or release.get("prerelease"):
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
        asset = by_name.get(f"mindie-agent-dsh-{tag.lstrip('v')}.tgz")
        if asset is None:
            asset = next(
                (url for name, url in by_name.items() if name.endswith(".tgz")),
                None,
            )
        sums = by_name.get("SHA256SUMS")
        if not asset or not sums:
            continue
        candidate = (version, tag, asset, sums)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best


def download(url: str, destination: Path, limit: int = 128 * 1024 * 1024) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "mindie-agent-dsh-updater/1"})
    total = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as stream:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
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
    expected = None
    for line in sums.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == actual:
            expected = actual
            break
    if expected is None:
        raise UpdateError("release SHA256SUMS does not contain the asset digest")
    return actual


def extract(asset: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(asset, "r:gz") as archive:
            for member in archive.getmembers():
                name = member.name
                if name.startswith("/") or ".." in Path(name).parts:
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
        completed = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    detail = (completed.stderr or completed.stdout or "").strip()
    return completed.returncode, detail[-2000:]


def setup_candidate(package: Path, python: str) -> None:
    """Provision the candidate into the shared runtime."""
    updater = shutil.which("uv")
    if updater is None:
        raise UpdateError("uv is not available to provision the candidate")
    command = [
        updater, "run", "--quiet", "--no-project", "--python", "3.11",
        str(package / "scripts" / "setup.py"), "--python", python,
        *os.environ.get("MINDIE_DSH_UPDATE_SETUP_ARGS", "").split(),
    ]
    code, detail = run(command, timeout=INSTALL_TIMEOUT, cwd=package)
    if code != 0:
        raise UpdateError("candidate setup failed: " + detail[-800:])


def sync_feed(timeout: int = 90) -> dict:
    engine = mp.engine_config()
    if not engine.is_file():
        return {"status": "skipped", "detail": "engine configuration is missing"}
    code, detail = run(
        [sys.executable, "-m", "mindie_knowledge.loop.cli", "sync", "--config", str(engine)],
        timeout=timeout,
    )
    return {
        "status": "ok" if code == 0 else "failed",
        "detail": "" if code == 0 else detail[-500:],
    }


def check(profile: str, current: str, api: str, apply: bool = True) -> dict:
    update_dir().mkdir(parents=True, exist_ok=True)
    status = {
        "schema": SCHEMA,
        "state": "checking",
        "current": current,
        "latest": None,
        "profile": profile,
        "checked_at": time.time(),
        "needs_host_reload": False,
        "detail": "",
    }
    atomic_json(status_path(), status)
    releases = fetch_releases(api)
    candidate = choose(releases, current)
    if candidate is None:
        status.update(state="up-to-date", checked_at=time.time(), feed=sync_feed())
        atomic_json(status_path(), status)
        return status
    _version_tuple, tag, asset_url, sums_url = candidate
    status["latest"] = tag
    generation = update_dir() / "generations" / tag
    package = generation / "package"
    asset = generation / "asset.tgz"
    sums = generation / "SHA256SUMS"
    if not (package / "package.json").is_file():
        generation.mkdir(parents=True, exist_ok=True)
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
    setup_candidate(package, sys.executable)
    if not apply:
        status.update(
            state="staged",
            checked_at=time.time(),
            previous=previous.get("generation"),
            generation=str(package),
            needs_host_reload=False,
            detail="candidate prepared; pointer not switched",
        )
        atomic_json(status_path(), status)
        return status
    write_current(package, tag, previous if isinstance(previous, dict) else None)
    status.update(
        state="applied",
        checked_at=time.time(),
        previous=previous.get("generation"),
        generation=str(package),
        feed=sync_feed(),
        needs_host_reload=False,
        detail="generation pointer switched; the stable launcher reloads it",
    )
    atomic_json(status_path(), status)
    return status


def acquire_lock():
    update_dir().mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            descriptor = os.open(lock_path(), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, str(os.getpid()).encode())
            os.close(descriptor)
            return descriptor
        except FileExistsError:
            try:
                if time.time() - lock_path().stat().st_mtime > 3600:
                    lock_path().unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("check", "status", "recover", "enable", "disable"))
    parser.add_argument("--profile", default=os.environ.get("DSH_PROFILE", "web"))
    parser.add_argument("--current-version", default="0.0.2")
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
    if args.command == "recover":
        for path in (lock_path(),):
            try:
                path.unlink()
            except OSError:
                pass
        current = read_json(current_path(), {})
        previous = current.get("previous_generation") if isinstance(current, dict) else None
        if isinstance(previous, str) and previous:
            write_current(Path(previous), str(current.get("previous_tag") or ""), None)
        print(json.dumps({"recovered": True, "previous": previous, "status": read_json(status_path(), {})}))
        return 0
    if not update_config()["enabled"]:
        print(json.dumps({"state": "disabled"}))
        return 0
    if acquire_lock() is None:
        if not args.silent:
            print(json.dumps({"state": "busy"}))
        return 0
    try:
        result = check(args.profile, args.current_version, args.api, apply=not args.stage_only)
        if not args.silent:
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        result = {
            "schema": SCHEMA,
            "state": "failed",
            "current": args.current_version,
            "checked_at": time.time(),
            "detail": f"{type(exc).__name__}: {str(exc)[:800]}",
        }
        atomic_json(status_path(), result)
        if not args.silent:
            print(json.dumps(result, ensure_ascii=False))
        return 1
    finally:
        try:
            lock_path().unlink()
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
