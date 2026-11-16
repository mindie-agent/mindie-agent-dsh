#!/usr/bin/env python3
"""[Role] Best-effort DSH storage lifecycle: historical sweep, component prune,
same-volume uv cache placement, and post-extraction archive removal.
[Invariants] Every `update/current.json` generation/previous generation
(explicit and default data homes), every DSH profile target (`DSH_HOME` and
`~/.dsh`), the live binding and every `dsh.json`/sibling-harness config pin stay
protected. Each managed generation/runtime holds a shared runtime lease across
every `sweep_legacy_installations` call, which accepts no `keep_paths`; an
unreadable rollback pointer therefore skips the sweep, and an unreadable config
defers orphan-engine deletion. Cleanup never replaces the primary status.
[Timeouts & Locks] One absolute budget; lease probes are non-blocking.
[Side-Effect Policy] Deletes only disposable historical directories/files and
creates only `.inuse.lock` leases plus the component cooldown marker.
"""
from __future__ import annotations

from contextlib import ExitStack
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
_CONFIG_BYTES = 2 * 1024 * 1024
_MANAGED_CONTAINERS = frozenset({
    "component-runtimes", "generations", "installations", "launch",
    "releases", "runtimes",
})
_HARNESS_CONFIGS = ("dsh.json", "config.json", "engine.json", "codex.json",
                    "cc.json", "kimi.json", "agy.json", "wb.json", "oc.json")


def _read_document(path: Path) -> tuple[dict[str, Any] | None, bool]:
    """Read one bounded JSON object; `False` flags a present-but-unusable file."""
    try:
        info = os.stat(path)
    except TimeoutError:
        raise
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    if info.st_size > _CONFIG_BYTES:
        return None, False
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except TimeoutError:
        raise
    except (OSError, ValueError):
        return None, False
    return (value, True) if isinstance(value, dict) else (None, False)


def _strip_win_prefix(text: str) -> str:
    if text[:8].upper() == "\\\\?\\UNC\\": return "\\\\" + text[8:]
    return text[4:] if text.startswith(("\\\\?\\", "//?/")) else text


def _strings(value: Any, *, absolute_only: bool = False, depth: int = 0) -> Iterable[str]:
    if depth > 12: return
    if isinstance(value, str):
        clean = _strip_win_prefix(value)
        if clean and (not absolute_only or Path(clean).is_absolute()):
            yield clean
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item, absolute_only=absolute_only, depth=depth + 1)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item, absolute_only=absolute_only, depth=depth + 1)


def _absolute_strings(value: Any) -> Iterable[str]:
    return _strings(value, absolute_only=True)


def _path_key(value: Any) -> str:
    text = _strip_win_prefix(os.fspath(value))
    return os.path.normcase(_strip_win_prefix(os.path.realpath(os.path.abspath(text))))


def _unique(values: Iterable[Any]) -> list[Path]:
    result, seen = [], set()
    for value in values:
        path = Path(_strip_win_prefix(os.fspath(value)))
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


def _platform_home(kind: str) -> Path:
    """The shared XDG-ignoring legacy home; `mp.*_home()` covers the DSH default."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    if kind == "data":
        base = (os.environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
                if os.name == "nt" else str(home / ".local" / "share"))
    else:
        base = (os.environ.get("APPDATA") or str(home / "AppData" / "Roaming")
                if os.name == "nt" else str(home / ".config"))
    return Path(base) / "mindie-agent" / "dsh"


def _dsh_data_homes() -> list[Path]:
    """Every DSH data home whose rollback pointer may still name a generation."""
    values = [mp.data_home(), _platform_home("data")]
    shared = os.environ.get("MINDIE_DATA_HOME")
    if isinstance(shared, str) and shared.strip():
        values.append(Path(shared).expanduser() / "dsh")
    return _unique(values)


def _dsh_config_homes() -> list[Path]:
    """Every harness config home that may name a generation, runtime or engine."""
    explicit, platform = mp.config_home(), _platform_home("config")
    values = [explicit, explicit.parent, platform, platform.parent]
    shared = os.environ.get("MINDIE_CONFIG_HOME")
    if isinstance(shared, str) and shared.strip():
        values.extend((Path(shared).expanduser() / "dsh", Path(shared).expanduser()))
    return _unique(values)


def _dsh_homes() -> list[Path]:
    """Explicit `DSH_HOME` plus the platform default profile home."""
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    values = [home / ".dsh"]
    explicit = os.environ.get("DSH_HOME")
    if isinstance(explicit, str) and explicit.strip():
        values.insert(0, Path(explicit).expanduser())
    return _unique(values)


def _profile_targets() -> list[Path]:
    """Resolve every DSH profile link; propagate unexpected I/O failures."""
    targets: list[Path] = []
    for home in _dsh_homes():
        try: entries = list((home / "profiles").iterdir())
        except TimeoutError: raise
        except FileNotFoundError: continue
        for profile in entries:
            link = profile / "node_modules" / "@mindie-agent" / "dsh"
            try:
                is_sym = link.is_symlink()
                is_junc = False if is_sym else _is_junction(link)
            except TimeoutError: raise
            except FileNotFoundError: continue
            if not (is_sym or is_junc): continue
            if is_sym:
                raw_link = os.readlink(link)
                hop = Path(raw_link) if os.path.isabs(raw_link) else link.parent / raw_link
                try: resolved = os.path.realpath(hop, strict=True)
                except FileNotFoundError: resolved = os.path.abspath(hop)
            else:
                try: resolved = os.path.realpath(link, strict=True)
                except FileNotFoundError: continue
            target = Path(_strip_win_prefix(resolved))
            if target != link: targets.append(target)
    return _unique(targets)


def _current_pins(update_dir: Path) -> tuple[list[Path], bool]:
    """`generation`/`previous_generation` and every absolute path they name."""
    current, readable = _read_document(Path(update_dir) / "current.json")
    if not readable or not isinstance(current, dict):
        return [], readable
    pins = [Path(item) for item in _absolute_strings(current)]
    pins.extend(Path(value) for key in ("generation", "previous_generation")
                if isinstance(value := current.get(key), str) and value
                and Path(value).is_absolute())
    return pins, True


def _collect_pins(extra_keep_paths: Iterable[Any] = (), *,
                  deadline: float | None = None) -> dict[str, Any]:
    """Union every rollback, profile, harness-config and caller pin.

    `rollback_complete` is false when a rollback pointer or the live binding
    cannot be inspected (skip every sweep); `config_complete` is false when a
    harness config exists but cannot be read (defer orphan-engine deletion).
    """
    pins: list[Path] = []
    warnings: list[str] = []
    rollback_complete = True
    config_complete = True
    for item in extra_keep_paths or ():
        try:
            pins.append(Path(item))
        except TypeError:
            continue
    updates = [home / "update" for home in _dsh_data_homes()]
    updates.extend(path for home in _dsh_config_homes()
                   for path in (home / "update", home / "state" / "update"))
    for update in _unique(updates):
        found, readable = _current_pins(update)
        pins.extend(found)
        if not readable:
            rollback_complete = False
            warnings.append("current " + update.parent.name[:24])
    if deadline is not None and time.monotonic() >= deadline:
        return {"pins": _unique(pins), "rollback_complete": False,
                "config_complete": False, "warnings": warnings + ["budget exhausted"]}
    try:
        pins.extend(_profile_targets())
    except TimeoutError:
        raise
    except (OSError, RuntimeError):
        rollback_complete = False
        warnings.append("profile binding")
    adapter = mp.config_file()
    config_files = _unique([adapter, *(
        home / name for home in _dsh_config_homes() for name in _HARNESS_CONFIGS)])
    for path in config_files:
        if deadline is not None and time.monotonic() >= deadline:
            config_complete = False
            warnings.append("budget exhausted")
            break
        value, readable = _read_document(path)
        if not readable:
            config_complete = False
            rollback_complete = rollback_complete and path != adapter
            warnings.append("adapter config" if path == adapter
                            else "config " + path.name[:24])
            continue
        if isinstance(value, dict):
            pins.extend(Path(item) for item in _absolute_strings(value))
    return {"pins": _unique(pins), "rollback_complete": rollback_complete,
            "config_complete": config_complete, "warnings": warnings}


def dsh_pins(*, keep_paths: Iterable[Any] = ()) -> list[Path]:
    """Absolute generations the live DSH binding still references."""
    return list(_collect_pins(keep_paths)["pins"])


def _dsh_roots() -> tuple[list[Path], list[Path]]:
    config = mp.config_home()
    data = mp.data_home()
    return _unique((config.parent, config)), _unique((data.parent, data))


def _uv_default_cache(env: dict[str, str]) -> Path:
    xdg = env.get("XDG_CACHE_HOME")
    if isinstance(xdg, str) and xdg.strip():
        return Path(xdg) / "uv"
    if os.name == "nt":
        base = env.get("LOCALAPPDATA") or os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "uv" / "cache"
    if sys.platform == "darwin":
        legacy = Path.home() / ".cache" / "uv"
        try:
            if legacy.exists() or legacy.is_symlink(): return legacy
        except TimeoutError: raise
        except OSError: pass
        return Path.home() / "Library" / "Caches" / "uv"
    return Path.home() / ".cache" / "uv"


def _device(path: Path) -> int | None:
    probe = Path(path)
    while True:
        try: return os.stat(probe).st_dev
        except FileNotFoundError:
            if probe.parent == probe: return None
            probe = probe.parent
        except TimeoutError: raise
        except OSError: return None


def configure_prebuild_uv_cache(env: dict[str, str], *, data_home: Path | None = None) -> dict[str, str]:
    """Pin cold-build uv cache to `<data_home>/.uv-cache` only when cross-volume."""
    if not isinstance(env, dict): return env
    explicit = env.get("UV_CACHE_DIR")
    if isinstance(explicit, str) and explicit.strip(): return env
    target = Path(data_home or mp.data_home()) / ".uv-cache"
    target_device = _device(target)
    if target_device is None: return env
    cache = _uv_default_cache(env)
    cache_devices = {_device(cache)}
    try: cache_devices.add(_device(cache.resolve()))
    except TimeoutError: raise
    except (OSError, RuntimeError): pass
    if target_device not in cache_devices:
        env["UV_CACHE_DIR"] = str(target)
    return env


def discard_archives(*paths: Path) -> None:
    """Remove an unpacked release tarball or its checksum without failing a switch."""
    for path in paths:
        try: Path(path).unlink(missing_ok=True)
        except TimeoutError: raise
        except OSError: pass


def _best_effort(call) -> dict[str, Any]:
    """Run one secondary cleanup stage; its deadline stays local and counted."""
    try: return call()
    except TimeoutError as exc:
        return {"status": "deferred", "partial": True, "warnings": [type(exc).__name__]}
    except Exception as exc:  # noqa: BLE001 - cleanup must never brick setup/update
        return {"status": "failed", "partial": True, "warnings": [type(exc).__name__]}


def _protected(*, config_roots, data_roots, codex_home, deadline):
    from mindie_knowledge.storage import collect_global_protected_roots
    from mindie_knowledge.storage.legacy_roots import protected_source_roots
    keep = collect_global_protected_roots(
        config_home=config_roots, data_home=data_roots, codex_home=codex_home, deadline=deadline)
    forbidden = protected_source_roots(config_home=config_roots, deadline=deadline)
    return list(keep), list(forbidden)


def _ancestor_link(path: Path) -> bool:
    """True when `path` or any ancestor is a symlink/junction, or unknown."""
    probe = Path(os.path.abspath(os.fspath(path)))
    for candidate in (probe, *probe.parents):
        try:
            if candidate.is_symlink() or _is_junction(candidate): return True
        except TimeoutError: raise
        except (OSError, ValueError): return True
    return False


def _engine_tails(text: str) -> set[tuple[str, str]]:
    """`("engines", "<name>.json")` tails named by one config string."""
    parts = os.path.normpath(text.replace("\\", "/")).split("/")
    return {(part.casefold(), parts[index + 1].casefold())
            for index, part in enumerate(parts[:-1])
            if part.casefold() == "engines" and parts[index + 1]}


def _engine_references(deadline: float) -> tuple[set[str], set[tuple[str, str]], list[str], bool]:
    """Scan every JSON file (including symlinks) in all relevant config homes."""
    keys, tails, warnings, complete = set(), set(), [], True
    homes = _dsh_config_homes()
    bases = _unique([*homes, *[home / "configs" for home in homes]])
    for home in bases:
        if time.monotonic() >= deadline:
            return keys, tails, warnings + ["budget exhausted"], False
        try:
            with os.scandir(home) as iterator:
                entries = [Path(home) / entry.name for entry in iterator
                           if (entry.is_file(follow_symlinks=False) or entry.is_symlink())
                           and entry.name.endswith(".json")]
        except TimeoutError: raise
        except FileNotFoundError: continue
        except OSError:
            complete = False
            warnings.append("scan " + home.name[:24])
            continue
        for path in entries:
            if time.monotonic() >= deadline:
                return keys, tails, warnings + ["budget exhausted"], False
            value, readable = _read_document(path)
            if not readable:
                complete = False
                warnings.append("config " + path.name[:24])
                continue
            for text in _strings(value):
                if Path(text).is_absolute():
                    keys.add(_path_key(text))
                tails.update(_engine_tails(text))
    return keys, tails, warnings, complete


def _protect_referenced_engines(references: set[str], tails: set[tuple[str, str]],
                                deadline: float, shielded: list[tuple[Path, Any]]) -> tuple[list[Path], list[str]]:
    """Return runtime pins and record mtime-shielded engine files for `sweep_legacy_installations`."""
    runtime_pins, warnings = [], []
    stems = {name[:-5] for _, name in tails if name.endswith(".json")}
    for home in _dsh_data_homes():
        for stem in stems:
            candidate = home / "runtimes" / stem
            if candidate.is_dir():
                runtime_pins.append(candidate)
    for cfg in _dsh_config_homes():
        engines = cfg / "engines"
        if time.monotonic() >= deadline:
            return runtime_pins, warnings + ["budget exhausted"]
        try:
            with os.scandir(engines) as iterator:
                files = [Path(engines) / e.name for e in iterator
                         if (e.is_file(follow_symlinks=False) or e.is_symlink())
                         and e.name.endswith(".json")]
        except TimeoutError: raise
        except FileNotFoundError: continue
        except OSError:
            warnings.append("engines " + cfg.name[:24])
            continue
        for path in files:
            if not _engine_referenced(path, references, tails):
                continue
            for data_root in _dsh_data_homes():
                rt = data_root / "runtimes" / path.stem
                if rt.is_dir(): runtime_pins.append(rt)
            try:
                before = os.stat(path)
                os.utime(path, None)
                shielded.append((path, before))
            except TimeoutError: raise
            except OSError: warnings.append("shield " + path.name[:24])
    return runtime_pins, warnings


def _engine_referenced(path: Path, references: set[str],
                       tails: set[tuple[str, str]]) -> bool:
    """Whether any config names this engine file, its spelling or its container."""
    for candidate in (path, Path(os.path.realpath(path))):
        key = _path_key(candidate)
        if key in references or os.path.dirname(key) in references:
            return True
    return (path.parent.name.casefold(), path.name.casefold()) in tails


def _prune_orphan_engines(*, keep_paths: list[Path], deadline: float) -> dict[str, Any]:
    """Remove direct `engines/<key>.json` files whose runtime no longer exists.

    The shared sweep's newest-two fallback is not enough: every harness config
    (including a sibling plugin borrowing this root) is scanned, and only a
    complete reference set plus Git/symlink/forbidden-root clearances may unlink.
    """
    from mindie_knowledge.filesystem import path_identity
    from mindie_knowledge.storage.legacy_roots import protected_source_roots
    from mindie_knowledge.storage.lifecycle import (
        has_git_ancestor,
        is_protected_path,
        is_within_roots,
    )

    runtimes_root = mp.data_home() / "runtimes"
    engines = mp.config_home() / "engines"
    if not runtimes_root.is_dir() or not engines.is_dir():
        return {"status": "absent", "reclaimed_files": 0, "warnings": []}
    if _ancestor_link(engines) or _ancestor_link(runtimes_root):
        return {"status": "refused_link", "reclaimed_files": 0, "warnings": []}
    if has_git_ancestor(engines) or has_git_ancestor(runtimes_root):
        return {"status": "protected", "reclaimed_files": 0, "warnings": []}
    try:
        forbidden = set(protected_source_roots(
            config_home=_dsh_config_homes(), deadline=deadline))
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
    references, tails, warnings, complete = _engine_references(deadline)
    if not complete:
        return {"status": "partial", "reclaimed_files": 0, "deferred_dirs": 0,
                "partial": True,
                "warnings": warnings[:8] + ["engine references incomplete"]}
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
    now = time.time()
    for path in entries:
        if time.monotonic() >= deadline:
            deferred += 1
            warnings.append("budget exhausted")
            break
        try:
            if path.is_symlink() or _is_junction(path) or path.stem in remaining:
                continue
            if path_identity(path) in protected:
                continue
            if (has_git_ancestor(path)
                    or is_within_roots(path, forbidden)
                    or is_protected_path(path, forbidden)):
                warnings.append("protected engine " + path.name[:24])
                continue
            if _engine_referenced(path, references, tails):
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
            "partial": bool(deferred), "warnings": warnings[:16]}


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


def _summary(stage: str, results: list[dict[str, Any]], *, started: float, available: bool = True) -> dict[str, Any]:
    counts = {name: 0 for name in _COUNT_FIELDS}
    warnings, statuses = 0, []
    for result in results:
        if not isinstance(result, dict): continue
        for name in _COUNT_FIELDS:
            value = result.get(name)
            if type(value) is int and value > 0: counts[name] += value
        items = result.get("warnings")
        if isinstance(items, list): warnings += len(items)
        statuses.append(str(result.get("status") or "unknown"))
    partial = any(status in {"partial", "deferred", "failed"} for status in statuses)
    failed = bool(statuses) and all(status in {"failed", "unavailable"} for status in statuses)
    status = "unavailable" if not available else ("failed" if failed else "partial" if partial else "completed")
    return {"schema": SCHEMA, "stage": stage, "status": status, "available": available, **counts,
            "partial": partial, "warnings": warnings, "stages": statuses,
            "elapsed_seconds": round(time.monotonic() - started, 3)}


def _managed_lease(target: Any, owning) -> Path | None:
    """The disposable generation/runtime directory one protected pin owns."""
    try: candidate = Path(_strip_win_prefix(os.fspath(target))).expanduser()
    except (TypeError, ValueError): return None
    if not candidate.is_absolute(): return None
    for item in (candidate, owning(str(candidate))):
        if item is None: continue
        clean = Path(_strip_win_prefix(os.fspath(item)))
        if not clean.name.startswith(".trash-"):
            name, parent = clean.name.casefold(), clean.parent.name.casefold()
            if (parent in _MANAGED_CONTAINERS or (parent == "dsh" and name == "venv")
                    or name in {"bootstrap-venv", "bootstrap-package"}):
                return clean
    return None


def _lease_protected(stack: ExitStack, keep_paths: Iterable[Any], *,
                     deadline: float) -> tuple[list[str], list[tuple[Path, Any]]]:
    """Hold a shared runtime lease in every protected managed generation.

    `sweep_legacy_installations` accepts no `keep_paths` and keeps a directory
    only when `is_lease_held(entry)` says so; these leases are the protection
    channel for the rollback generation, profile targets and config-pinned
    runtimes across every sweep call until the `ExitStack` unwinds.
    """
    from mindie_knowledge.storage import acquire_runtime_lease, owning_dir

    warnings: list[str] = []
    times: list[tuple[Path, Any]] = []
    seen: set[str] = set()
    for raw in keep_paths:
        if time.monotonic() >= deadline:
            warnings.append("budget exhausted")
            break
        item = _managed_lease(raw, owning_dir)
        if item is None:
            continue
        key = _path_key(item)
        if key in seen:
            continue
        seen.add(key)
        try:
            if not item.is_dir() or item.is_symlink() or _is_junction(item):
                continue
            before = os.stat(item)
            stack.enter_context(acquire_runtime_lease(item))
            _restore_times(item, before)
            times.append((item, before))
        except TimeoutError:
            raise
        except (OSError, RuntimeError):
            warnings.append("lease " + item.name[:24])
    return warnings, times


def _restore_times(path: Path, info: Any) -> None:
    # A governance lease must never displace an entry's retention ranking.
    try:
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
    except TimeoutError:
        raise
    except OSError:
        pass


def sweep(*, stage: str, home_roots: bool = True, force: bool = False,
          budget_seconds: float = BUDGET_SECONDS,
          keep_paths: Iterable[Any] | None = None) -> dict[str, Any]:
    """Sweep four legacy DSH layouts plus orphaned component/uv caches.

    `home_roots` adds the shared production home scan; the explicit DSH roots
    always run, so a custom `MINDIE_DSH_DATA_HOME` is covered too. Every
    rollback/profile/config pin plus the caller's `keep_paths` holds a runtime
    lease for the whole sweep so the shared helper cannot retire it.
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
    pins = _best_effort(lambda: _collect_pins(
        keep_paths or (), deadline=deadline))
    if not isinstance(pins, dict) or not pins.get("rollback_complete"):
        timed_out = isinstance(pins, dict) and pins.get("status") == "deferred"
        status = "deferred" if timed_out else "failed"
        warnings = (pins.get("warnings") if isinstance(pins, dict) else None) \
            or ["binding_pins_unavailable"]
        return _summary(stage, [{"status": status, "partial": True,
                                 "warnings": warnings}], started=started)
    merged = pins["pins"]
    results: list[dict[str, Any]] = []
    if not pins.get("config_complete", True):
        results.append({"status": "partial", "partial": True,
                        "warnings": pins.get("warnings") or ["config_pins_partial"]})
    eng_refs, eng_tails, eng_warnings, eng_complete = _engine_references(deadline)
    if not eng_complete:
        results.append({"status": "partial", "partial": True,
                        "warnings": eng_warnings[:8] + ["engine references incomplete"]})
    stack = ExitStack()
    leased_times: list[tuple[Path, Any]] = []
    try:
        rt_pins, shield_warns = (
            _protect_referenced_engines(eng_refs, eng_tails, deadline, leased_times)
            if eng_complete else ([], []))
        merged = _unique([*merged, *rt_pins, *(Path(p) for p in eng_refs)])
        lease_warnings, pin_times = _lease_protected(stack, merged, deadline=deadline)
        leased_times.extend(pin_times)
        all_lease_warnings = [*lease_warnings, *shield_warns]
        if all_lease_warnings:
            results.append({"status": "partial", "partial": True,
                            "warnings": all_lease_warnings[:8]})
        can_sweep_legacy = eng_complete and not all_lease_warnings and pins.get("config_complete", True)
        if can_sweep_legacy and home_roots:
            results.append(_best_effort(lambda: sweep_legacy_installations(
                budget_seconds=max(0.1, deadline - time.monotonic()),
                force=force, deadline=deadline)))
        if can_sweep_legacy:
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
                live, complete = live_process_prefixes(
                    timeout=min(2.0, max(0.1, deadline - time.monotonic())))
                return prune_component_runtimes(
                    root, keep_paths=[*merged, *protected[0]],
                    keep_recent_verified=2, staging_grace_seconds=1800.0,
                    budget_seconds=max(0.1, deadline - time.monotonic()),
                    live_paths=live, inspection_complete=complete,
                    include_global_roots=False, forbidden_roots=protected[1],
                    deadline=deadline)
            results.append(_best_effort(prune))
        if eng_complete:
            results.append(_best_effort(lambda: _prune_orphan_engines(
                keep_paths=merged, deadline=deadline)))
        results.append(_best_effort(lambda: _discard_retained_archives(deadline=deadline)))
    except TimeoutError as exc:
        results.append({"status": "deferred", "partial": True,
                        "warnings": [type(exc).__name__]})
    finally:
        stack.close()
        for leased_path, leased_info in leased_times:
            _restore_times(leased_path, leased_info)
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
