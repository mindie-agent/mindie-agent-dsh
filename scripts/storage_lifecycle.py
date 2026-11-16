#!/usr/bin/env python3
"""[Role] Best-effort DSH storage lifecycle: historical sweep, component prune,
same-volume uv cache placement, and post-extraction archive removal.
[Invariants] The runtimes selected by `config.json`, `update/current.json`
`generation`/`previous_generation`, DSH profile symlink targets, live leases or
processes, and cross-plugin source roots stay protected; cleanup never replaces
the primary setup/update status. A cleanup deadline is reported as a deferred
secondary result and is never re-raised into the already-published state.
[Timeouts & Locks] Each sweep is bounded by an absolute budget; shared helpers
receive that deadline and never wait for a busy lease or builder lock.
[Side-Effect Policy] Deletes only disposable MindIE-owned historical
directories and the unpacked release tarball; the shared component writes its
own cooldown marker. No network, model, or configuration write.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterable

import mindie_paths as mp

BUDGET_SECONDS = 6.0
SCHEMA = "mindie-dsh-storage-gc/1"
_COUNT_FIELDS = (
    "reclaimed_dirs",
    "deferred_dirs",
    "kept_dirs",
    "reclaimed_trash",
    "reclaimed_files",
)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _absolute_strings(value: Any, depth: int = 0) -> Iterable[str]:
    if depth > 12:
        return
    if isinstance(value, str):
        if value and Path(value).is_absolute():
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _absolute_strings(item, depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _absolute_strings(item, depth + 1)


def _unique(values: Iterable[Any]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for value in values:
        path = Path(value)
        key = os.path.normcase(os.path.abspath(os.fspath(path)))
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def _is_junction(path: Path) -> bool:
    try:
        from mindie_knowledge.filesystem import is_junction
    except ImportError:
        checker = getattr(os.path, "isjunction", None)
        if checker is None:
            return False
        is_junction = checker
    try:
        return bool(is_junction(path))
    except TimeoutError:
        raise
    except OSError:
        return False


def _profile_targets() -> list[Path]:
    """Resolve DSH profile links to the generation they currently load."""
    home = Path(os.environ.get("DSH_HOME")
                or Path(os.environ.get("USERPROFILE") or Path.home()) / ".dsh")
    profiles = home / "profiles"
    try:
        entries = list(profiles.iterdir())
    except TimeoutError:
        raise
    except OSError:
        return []
    targets: list[Path] = []
    for profile in entries:
        link = profile / "node_modules" / "@mindie-agent" / "dsh"
        try:
            if not (link.is_symlink() or _is_junction(link)):
                continue
            target = Path(os.path.realpath(link))
        except TimeoutError:
            raise
        except OSError:
            continue
        if target != link:
            targets.append(target)
    return targets


def dsh_pins() -> list[Path]:
    """Absolute generations the live DSH binding still references."""
    pins: list[Path] = []
    for path in (mp.config_file(), mp.data_home() / "update" / "current.json"):
        value = _read_json(path)
        if value is not None:
            pins.extend(Path(item) for item in _absolute_strings(value))
    current = _read_json(mp.data_home() / "update" / "current.json") or {}
    for key in ("generation", "previous_generation"):
        value = current.get(key)
        if isinstance(value, str) and value and Path(value).is_absolute():
            pins.append(Path(value))
    pins.extend(_profile_targets())
    return _unique(pins)


def _dsh_roots() -> tuple[list[Path], list[Path]]:
    config = mp.config_home()
    data = mp.data_home()
    return _unique((config.parent, config)), _unique((data.parent, data))


def _uv_default_cache(env: dict[str, str]) -> Path:
    xdg = env.get("XDG_CACHE_HOME")
    if isinstance(xdg, str) and xdg.strip():
        return Path(xdg) / "uv"
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") \
            or str(Path.home() / "AppData" / "Local")
        return Path(base) / "uv" / "cache"
    if sys.platform == "darwin":
        legacy = Path.home() / ".cache" / "uv"
        try:
            if legacy.exists() or legacy.is_symlink():
                return legacy
        except TimeoutError:
            raise
        except OSError:
            pass
        return Path.home() / "Library" / "Caches" / "uv"
    return Path.home() / ".cache" / "uv"


def _device(path: Path) -> int | None:
    probe = Path(path)
    while True:
        try:
            return os.stat(probe).st_dev
        except FileNotFoundError:
            if probe.parent == probe:
                return None
            probe = probe.parent
        except TimeoutError:
            raise
        except OSError:
            return None


def configure_prebuild_uv_cache(env: dict[str, str], *,
                                data_home: Path | None = None) -> dict[str, str]:
    """Pin cold-build uv cache to `<data_home>/.uv-cache` only when needed.

    An explicit non-empty caller value always wins. The platform default is
    compared on device identity; a same-device default is left untouched so a
    normal user cache stays shared and warm. This mirrors the shared
    `mindie_knowledge.storage.configure_same_volume_uv_cache` contract while
    remaining importable before the runtime component is installed.
    """
    if not isinstance(env, dict):
        return env
    explicit = env.get("UV_CACHE_DIR")
    if isinstance(explicit, str) and explicit.strip():
        return env
    base = Path(data_home or mp.data_home())
    target = base / ".uv-cache"
    target_device = _device(target)
    if target_device is None:
        return env
    cache = _uv_default_cache(env)
    cache_devices = {_device(cache)}
    try:
        cache_devices.add(_device(cache.resolve()))
    except TimeoutError:
        raise
    except (OSError, RuntimeError):
        pass
    if target_device in cache_devices:
        return env
    env["UV_CACHE_DIR"] = str(target)
    return env


def discard_archives(*paths: Path) -> None:
    """Remove an unpacked release tarball or its checksum without failing a switch."""
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except TimeoutError:
            raise
        except OSError:
            pass


def _best_effort(call) -> dict[str, Any]:
    """Run one secondary cleanup stage; its deadline stays local and counted."""
    try:
        return call()
    except TimeoutError as exc:
        return {"status": "deferred", "partial": True,
                "warnings": [type(exc).__name__]}
    except Exception as exc:  # noqa: BLE001 - cleanup must never brick setup/update
        return {"status": "failed", "partial": True,
                "warnings": [type(exc).__name__]}


def _protected(*, config_roots, data_roots, codex_home, deadline):
    from mindie_knowledge.storage import collect_global_protected_roots
    from mindie_knowledge.storage.legacy_roots import protected_source_roots
    keep = collect_global_protected_roots(
        config_home=config_roots, data_home=data_roots,
        codex_home=codex_home, deadline=deadline)
    forbidden = protected_source_roots(
        config_home=config_roots, deadline=deadline)
    return list(keep), list(forbidden)


def _prune_orphan_engines(*, keep_paths: list[Path], deadline: float) -> dict[str, Any]:
    """Remove direct `engines/<key>.json` files whose runtime no longer exists.

    The shared sweep keeps the newest two engine files as a generic fallback;
    DSH additionally knows which `runtimes/<key>` directories survived and
    which engine paths the live binding references. Only direct JSON children
    with no live runtime and no live pin are removed, so a configured domain
    route or a recent staging build is always retained.
    """
    from mindie_knowledge.filesystem import path_identity
    from mindie_knowledge.storage.lifecycle import has_git_ancestor

    runtimes_root = mp.data_home() / "runtimes"
    engines = mp.config_home() / "engines"
    if (runtimes_root.is_symlink() or _is_junction(runtimes_root)
            or engines.is_symlink() or _is_junction(engines)):
        return {"status": "refused_link", "reclaimed_files": 0, "warnings": []}
    if not runtimes_root.is_dir() or not engines.is_dir():
        return {"status": "absent", "reclaimed_files": 0, "warnings": []}
    if has_git_ancestor(engines) or has_git_ancestor(runtimes_root):
        return {"status": "protected", "reclaimed_files": 0, "warnings": []}
    try:
        with os.scandir(runtimes_root) as iterator:
            remaining = {
                entry.name for entry in iterator
                if entry.is_dir(follow_symlinks=False)
            }
        with os.scandir(engines) as iterator:
            entries = [Path(engines) / entry.name for entry in iterator
                       if entry.is_file(follow_symlinks=False)
                       and entry.name.endswith(".json")]
    except TimeoutError:
        raise
    except OSError as exc:
        return {"status": "unreadable", "reclaimed_files": 0,
                "warnings": [type(exc).__name__]}
    protected = set()
    for path in keep_paths:
        try:
            protected.add(path_identity(path))
        except TimeoutError:
            raise
        except (OSError, RuntimeError):
            continue
    reclaimed = 0
    deferred = 0
    warnings: list[str] = []
    now = time.time()
    for path in entries:
        if time.monotonic() >= deadline:
            deferred += 1
            warnings.append("budget exhausted")
            break
        try:
            if path.is_symlink() or path.stem in remaining:
                continue
            if path_identity(path) in protected:
                continue
            if now - path.stat().st_mtime < 1800.0:
                continue
            path.unlink(missing_ok=True)
            reclaimed += 1
        except TimeoutError:
            raise
        except OSError as exc:
            warnings.append("engine " + type(exc).__name__)
    return {"status": "partial" if deferred else "completed",
            "reclaimed_files": reclaimed, "deferred_dirs": deferred,
            "partial": bool(deferred), "warnings": warnings}


def _discard_retained_archives(*, deadline: float) -> dict[str, Any]:
    """Drop historical tarballs from generations that already have a package.

    The shared sweep skips a retained generation whose child `package/` is a
    configured pin, so its sibling `asset.tgz` would otherwise survive. The
    extracted payload is authoritative; only a verified `package/package.json`
    unlocks archive removal.
    """
    generations = mp.data_home() / "update" / "generations"
    if generations.is_symlink() or _is_junction(generations):
        return {"status": "refused_link", "reclaimed_files": 0, "warnings": []}
    from mindie_knowledge.storage.lifecycle import has_git_ancestor
    if has_git_ancestor(generations):
        return {"status": "protected", "reclaimed_files": 0, "warnings": []}
    if not generations.is_dir():
        return {"status": "absent", "reclaimed_files": 0, "warnings": []}
    reclaimed = 0
    deferred = 0
    warnings: list[str] = []
    try:
        with os.scandir(generations) as iterator:
            entries = [Path(entry.path) for entry in iterator
                       if entry.is_dir(follow_symlinks=False)]
    except TimeoutError:
        raise
    except OSError as exc:
        return {"status": "unreadable", "reclaimed_files": 0,
                "warnings": [type(exc).__name__]}
    for entry in entries:
        if time.monotonic() >= deadline:
            deferred += 1
            warnings.append("budget exhausted")
            break
        if (entry.is_symlink() or ".git" in entry.parts
                or (entry / ".git").exists()):
            continue
        if not (entry / "package" / "package.json").is_file():
            continue
        for name in ("asset.tgz", "SHA256SUMS"):
            target = entry / name
            try:
                if target.is_symlink() or not target.is_file():
                    continue
                target.unlink(missing_ok=True)
                reclaimed += 1
            except TimeoutError:
                raise
            except OSError as exc:
                warnings.append(name + ": " + type(exc).__name__)
    return {"status": "partial" if deferred else "completed",
            "reclaimed_files": reclaimed, "deferred_dirs": deferred,
            "partial": bool(deferred), "warnings": warnings}


def _summary(stage: str, results: list[dict[str, Any]], *,
             started: float, available: bool = True) -> dict[str, Any]:
    counts = {name: 0 for name in _COUNT_FIELDS}
    warnings = 0
    statuses: list[str] = []
    for result in results:
        if not isinstance(result, dict):
            continue
        for name in _COUNT_FIELDS:
            value = result.get(name)
            if type(value) is int and value > 0:
                counts[name] += value
        items = result.get("warnings")
        if isinstance(items, list):
            warnings += len(items)
        statuses.append(str(result.get("status") or "unknown"))
    partial = any(status in {"partial", "deferred", "failed"}
                  for status in statuses)
    failed = bool(statuses) and all(status in {"failed", "unavailable"}
                                    for status in statuses)
    if not available:
        status = "unavailable"
    else:
        status = "failed" if failed else "partial" if partial else "completed"
    return {
        "schema": SCHEMA,
        "stage": stage,
        "status": status,
        "available": available,
        **counts,
        "partial": partial,
        "warnings": warnings,
        "stages": statuses,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def sweep(*, stage: str, home_roots: bool = True, force: bool = False,
          budget_seconds: float = BUDGET_SECONDS) -> dict[str, Any]:
    """Sweep four legacy DSH layouts plus orphaned component/uv caches.

    `home_roots` includes the shared production home scan (which is how the
    orphaned `~/.cache/mindie-agent-dsh/uv-cache` is discovered). The explicit
    DSH roots always run, so a custom `MINDIE_DSH_DATA_HOME` is covered too.
    """
    started = time.monotonic()
    deadline = started + max(0.1, float(budget_seconds))
    try:
        from mindie_knowledge.storage import (
            live_process_prefixes,
            prune_component_runtimes,
            sweep_legacy_installations,
        )
    except TimeoutError:
        raise
    except ImportError:
        return _summary(stage, [], started=started, available=False)
    config_roots, data_roots = _dsh_roots()
    codex_home = Path(os.environ.get("CODEX_HOME")
                      or (Path.home() / ".codex"))
    pins = _best_effort(dsh_pins)
    keep_paths = pins if isinstance(pins, list) else None
    if keep_paths is None:
        return _summary(stage, [{"status": "failed", "partial": True,
                                 "warnings": ["binding_pins_unavailable"]}],
                        started=started)
    results: list[dict[str, Any]] = []
    if home_roots:
        results.append(_best_effort(lambda: sweep_legacy_installations(
            budget_seconds=max(0.1, deadline - time.monotonic()),
            force=force, deadline=deadline)))
    results.append(_best_effort(lambda: sweep_legacy_installations(
        config_home=config_roots, data_home=data_roots, codex_home=codex_home,
        budget_seconds=max(0.1, deadline - time.monotonic()),
        force=force, deadline=deadline)))
    root = mp.data_home() / "update" / "component-runtimes"
    if root.is_dir():
        def prune() -> dict[str, Any]:
            protected = _protected(
                config_roots=config_roots, data_roots=data_roots,
                codex_home=codex_home, deadline=deadline)
            forbidden = protected[1]
            live, complete = live_process_prefixes(
                timeout=min(2.0, max(0.1, deadline - time.monotonic())))
            return prune_component_runtimes(
                root, keep_paths=[*keep_paths, *protected[0]],
                keep_recent_verified=2, staging_grace_seconds=1800.0,
                budget_seconds=max(0.1, deadline - time.monotonic()),
                live_paths=live, inspection_complete=complete,
                include_global_roots=False, forbidden_roots=forbidden,
                deadline=deadline)
        results.append(_best_effort(prune))
    results.append(_best_effort(lambda: _prune_orphan_engines(
        keep_paths=keep_paths, deadline=deadline)))
    results.append(_best_effort(lambda: _discard_retained_archives(
        deadline=deadline)))
    return _summary(stage, results, started=started)


def safe_sweep(*, stage: str, **kwargs) -> dict[str, Any]:
    """Run one secondary sweep without letting its failure replace a switch.

    `sweep` already degrades every shared helper, but path resolution or a
    caller-supplied monkeypatch can still fail. The explicit deadline branch
    keeps that timeout classified as deferred rather than an internal update
    failure; the primary setup/update status has already been published.
    """
    started = time.monotonic()
    try:
        return sweep(stage=stage, **kwargs)
    except TimeoutError as exc:
        return _summary(stage, [{"status": "deferred", "partial": True,
                                 "warnings": [type(exc).__name__]}],
                        started=started)
    except Exception as exc:  # noqa: BLE001 - secondary cleanup boundary
        return _summary(stage, [{"status": "failed", "partial": True,
                                 "warnings": [type(exc).__name__]}],
                        started=started)


__all__ = [
    "BUDGET_SECONDS",
    "configure_prebuild_uv_cache",
    "discard_archives",
    "dsh_pins",
    "safe_sweep",
    "sweep",
]
