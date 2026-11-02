"""[Role] Install the selected DSH bundle through its native plugin CLI.
[Invariants] Only this bundle's dependency is changed. The candidate's declared
DSH requirement (`engines.dsh`, else the `@deepseek-ai/dsh` peer) must admit the
running runtime. Unknown native writes are retained in the existing update
status and are never repeated blindly.
[Timeouts & Locks] Native reads take 30s and installation 120s, plus the shared
owned runner's cleanup allowance. The updater owns its existing check lock.
[Side-Effect Policy] Runs `dsh plugin add link:... --ignore-scripts`, then reads
the native dependency back. Loaded profile definitions require native reload.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil

from mindie_knowledge.loop.process import bounded_run

from semver_range import SemverRangeError, satisfies


def installed(native, profile):
    output = bounded_run([native, 'plugin', '--profile', profile, 'list', '--json'], '',
                         timeout=30, max_output=256 * 1024)
    rows = json.loads(output)
    if len(rows) != 1:
        raise ValueError('native plugin profile readback is ambiguous')
    row = rows[0].get('dependencies', {}).get('mindie-agent-dsh')
    return str(Path(row['path']).resolve()) if row is not None else None



def current_version(profile):
    """Read the registered bundle, which can lag a legacy runtime-only update."""
    native = shutil.which('dsh')
    if native is None:
        raise FileNotFoundError('dsh is required to inspect its installed plugin')
    package = installed(native, profile)
    if package is None:
        return None
    manifest = json.loads((Path(package) / 'package.json').read_text(encoding='utf-8'))
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
    native = shutil.which('dsh')
    if native is None:
        raise FileNotFoundError('dsh is required to install its native plugin update')
    package = Path(package).resolve()
    declared = json.loads((package / 'package.json').read_text(encoding='utf-8'))
    version = bounded_run([native, '--version'], '', timeout=15, max_output=4096).strip()
    if not compatible(declared, version):
        raise ValueError('candidate plugin does not declare compatibility with the installed DSH')
    before = installed(native, profile)
    if before == str(package):
        status['native'] = dict(stage='verified', target=str(package), changed=False)
        status['needs_host_reload'] = True
        record(status)
        return
    status['native'] = dict(stage='installing', target=str(package), previous=before)
    record(status)
    cleanup = dict(cleanup_failed=False)
    error = None
    try:
        bounded_run([native, 'plugin', '--profile', profile, 'add', 'link:' + str(package),
                     '--ignore-scripts'], '', timeout=120, max_output=256 * 1024,
                    cleanup_receipt=cleanup)
    except Exception as exc:
        error = exc
    if cleanup['cleanup_failed']:
        status['native'].update(stage='unknown', cleanup_failed=True)
        record(status)
        raise RuntimeError('native plugin writer cleanup is unconfirmed') from error
    try:
        observed = installed(native, profile)
    except Exception:
        status['native'].update(stage='unknown', cleanup_failed=False)
        record(status)
        raise
    if observed == str(package):
        status['native'].update(stage='verified', changed=True)
        status['needs_host_reload'] = True
        if error:
            status['native']['command_error_type'] = type(error).__name__
        record(status)
        return
    status['native'].update(stage='failed' if observed == before else 'unknown', observed=observed)
    record(status)
    if error:
        raise error
    raise RuntimeError('native plugin readback differs from the selected candidate')
