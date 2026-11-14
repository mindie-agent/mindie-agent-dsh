"""[Role] Install the selected DSH bundle through its native plugin CLI.
[Invariants] Only this bundle's dependency is changed. The candidate's declared
DSH requirement (`engines.dsh`, else the `@deepseek-ai/dsh` peer) must admit the
running runtime. Unknown native writes are retained in the existing update
status and are never repeated blindly. A missing plugin package.json names that
artifact and does not include the local path.
[Timeouts & Locks] Native reads take 30s and installation 120s, plus the shared
owned runner's cleanup allowance. The updater owns its existing check lock.
[Side-Effect Policy] Runs `dsh plugin add link:... --ignore-scripts`, then reads
the native dependency back. Loaded profile definitions require native reload.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402
from mindie_knowledge.loop.process import bounded_run

from semver_range import SemverRangeError, satisfies


def _resolve_cli(missing: str) -> list[str]:
    found = shutil.which('dsh')
    if found and os.name == 'nt':
        try:
            if 'windowsapps' in found.replace('/', '\\').lower() and Path(found).stat().st_size == 0:
                found = None
        except TimeoutError:
            raise
        except OSError:
            found = None
    if found is None:
        found = mp.which_native('dsh')
    if found is None:
        raise FileNotFoundError(missing)
    if os.name == 'nt':
        unwrapped = mp.unwrap_cli_argv(found)
        if unwrapped is not None:
            return unwrapped
        p = Path(found)
        if p.suffix.lower() in {'.cmd', '.bat'} or p.is_file():
            raise FileNotFoundError(missing)
    return [found]


def matches_dsh_peer(requirement: str, version: str) -> bool:
    """Evaluate exact or comparator-range DSH peerDependencies with includePrerelease semantics."""
    try:
        return satisfies(version, requirement)
    except SemverRangeError:
        return False


def plugin_manifest(package, missing):
    """Read package.json, or name that artifact when the file is gone."""
    path = Path(package) / 'package.json'
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise FileNotFoundError(missing) from None


def installed(native_argv, profile):
    argv_prefix = [native_argv] if isinstance(native_argv, str) else list(native_argv)
    output = bounded_run([*argv_prefix, 'plugin', '--profile', profile, 'list', '--json'], '',
                         timeout=30, max_output=256 * 1024)
    rows = json.loads(output)
    if len(rows) != 1:
        raise ValueError('native plugin profile readback is ambiguous')
    row = rows[0].get('dependencies', {}).get('mindie-agent-dsh')
    return mp.strip_win_extended_prefix(str(Path(row['path']).resolve())) if row is not None else None



def current_version(profile):
    """Read the registered bundle, which can lag a legacy runtime-only update."""
    native_argv = _resolve_cli('dsh is required to inspect its installed plugin')
    package = installed(native_argv, profile)
    if package is None:
        return None
    manifest = plugin_manifest(package, 'installed native plugin package.json is unavailable')
    if manifest.get('name') != 'mindie-agent-dsh' or not isinstance(manifest.get('version'), str):
        raise ValueError('native DSH bundle identity is unavailable')
    return manifest['version']


def dsh_requirement(declared):
    """Return the DSH requirement clause; `engines.dsh` decides alone, as in DSH."""
    engines = declared.get('engines')
    if isinstance(engines, dict) and 'dsh' in engines:
        return engines['dsh']
    peers = declared.get('peerDependencies')
    if isinstance(peers, dict) and isinstance(peers.get('@deepseek-ai/dsh'), str):
        return peers['@deepseek-ai/dsh']
    raise ValueError('candidate plugin declares no DSH version requirement')


def compatible(declared, version):
    """Whether the candidate's declared DSH requirement admits this runtime."""
    try:
        return satisfies(version, dsh_requirement(declared))
    except SemverRangeError as error:
        raise ValueError(
            f'candidate plugin declares an unsupported DSH requirement: {error}'
        ) from error


def install(package, profile, status, record):
    native_argv = _resolve_cli('dsh is required to install its native plugin update')
    package = Path(package).resolve()
    package_str = mp.strip_win_extended_prefix(str(package))
    declared = plugin_manifest(package, 'candidate plugin package.json is unavailable')
    version = bounded_run([*native_argv, '--version'], '', timeout=15, max_output=4096).strip()
    if not compatible(declared, version):
        raise ValueError('candidate plugin does not declare compatibility with the installed DSH')
    before = installed(native_argv, profile)
    if mp.same_path(before, package_str):
        status['native'] = dict(stage='verified', target=package_str, changed=False)
        status['needs_host_reload'] = True
        record(status)
        return
    status['native'] = dict(stage='installing', target=package_str, previous=before)
    record(status)
    cleanup = dict(cleanup_failed=False)
    error = None
    try:
        bounded_run([*native_argv, 'plugin', '--profile', profile, 'add', 'link:' + package_str,
                     '--ignore-scripts'], '', timeout=120, max_output=256 * 1024,
                    cleanup_receipt=cleanup)
    except Exception as exc:
        error = exc
    if cleanup['cleanup_failed']:
        status['native'].update(stage='unknown', cleanup_failed=True)
        record(status)
        raise RuntimeError('native plugin writer cleanup is unconfirmed') from error
    try:
        observed = installed(native_argv, profile)
    except Exception:
        status['native'].update(stage='unknown', cleanup_failed=False)
        record(status)
        raise
    if mp.same_path(observed, package_str):
        status['native'].update(stage='verified', changed=True)
        status['needs_host_reload'] = True
        if error:
            status['native']['command_error_type'] = type(error).__name__
        record(status)
        return
    status['native'].update(stage='failed' if mp.same_path(observed, before) else 'unknown', observed=observed)
    record(status)
    if error:
        raise error
    raise RuntimeError('native plugin readback differs from the selected candidate')
