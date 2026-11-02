"""Native dependency readback distinguishes applied, failed, and unknown writes."""
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import native_update
import updater


def package(tmp_path, **manifest):
    path = tmp_path / 'candidate'
    path.mkdir()
    declared = dict(
        name='mindie-agent-dsh', version='0.1.10',
        peerDependencies={'@deepseek-ai/dsh': '>=0.2.0-rc.2 <0.2.2-0'})
    declared.update(manifest)
    (path / 'package.json').write_text(json.dumps(declared))
    return path


def runner(monkeypatch, target, *, uncertain=False, applied=True, version='0.2.1-rc.5'):
    calls = []
    reads = []
    # This is the actual DSH 0.2.1-rc.5 pnpm list shape; profile paths are
    # test-owned. Other installed dependencies must remain outside our write.
    def command(argv, _payload, **kwargs):
        calls.append(argv)
        if argv[1:] == ['--version']:
            return version + '\n'
        if 'list' in argv:
            selected = str(target.resolve()) if reads and applied else str(target.parent / 'old')
            reads.append(selected)
            return json.dumps([dict(name='dsh-profile-web', path=str(target.parent), private=True,
                dependencies={'mindie-agent-dsh': {'from': 'mindie-agent-dsh', 'version': 'link:../candidate', 'path': selected},
                              'unrelated-plugin': dict(version='1.0.0', path='/unchanged')})])
        if uncertain:
            kwargs['cleanup_receipt']['cleanup_failed'] = True
            raise TimeoutError('native writer drain unconfirmed')
        if not applied:
            raise RuntimeError('native install failed')
        return 'updated native dependency\n'
    monkeypatch.setattr(native_update.shutil, 'which', lambda _: '/native/dsh')
    monkeypatch.setattr(native_update, 'bounded_run', command)
    return calls, reads


def test_only_selected_native_dependency_is_installed_and_read_back(tmp_path, monkeypatch):
    target = package(tmp_path)
    calls, _ = runner(monkeypatch, target)
    status, receipts = {}, []
    native_update.install(target, 'web', status, lambda row: receipts.append(json.loads(json.dumps(row))))
    install = next(command for command in calls if 'add' in command)
    assert install == ['/native/dsh', 'plugin', '--profile', 'web', 'add', 'link:' + str(target.resolve()), '--ignore-scripts']
    assert receipts[0]['native']['stage'] == 'installing'
    assert status['native']['stage'] == 'verified' and status['needs_host_reload']


def test_failed_install_with_old_native_readback_stays_failed(tmp_path, monkeypatch):
    target = package(tmp_path)
    runner(monkeypatch, target, applied=False)
    status = {}
    with pytest.raises(RuntimeError, match='native install failed'):
        native_update.install(target, 'web', status, lambda _: None)
    assert status['native']['stage'] == 'failed'


def test_unsettled_native_write_is_preserved_and_not_repeated(tmp_path, monkeypatch):
    target = package(tmp_path)
    calls, reads = runner(monkeypatch, target, uncertain=True)
    status = {}
    with pytest.raises(RuntimeError, match='cleanup is unconfirmed'):
        native_update.install(target, 'web', status, lambda row: updater.atomic_json(updater.status_path(), row))
    assert len(reads) == 1
    assert status['native']['stage'] == 'unknown'
    before = len(calls)
    with pytest.raises(updater.UpdateError, match='unconfirmed'):
        updater.check('web', '0.1.1', updater.DEFAULT_API)
    assert len(calls) == before


def test_declared_range_admits_current_runtime(tmp_path, monkeypatch):
    target = package(tmp_path)
    runner(monkeypatch, target, version='0.2.1-rc.5')
    status = {}
    native_update.install(target, 'web', status, lambda _: None)
    assert status['native']['stage'] == 'verified'


def test_runtime_outside_declared_range_is_rejected(tmp_path, monkeypatch):
    target = package(tmp_path)
    runner(monkeypatch, target, version='0.3.0-rc.1')
    with pytest.raises(ValueError, match='does not declare compatibility'):
        native_update.install(target, 'web', {}, lambda _: None)


def test_engines_dsh_decides_alone(tmp_path, monkeypatch):
    target = package(tmp_path, engines={'dsh': '>=0.2.1-rc.5 <0.2.2-0'},
                     peerDependencies={'@deepseek-ai/dsh': '0.1.0'})
    runner(monkeypatch, target, version='0.2.1-rc.5')
    status = {}
    native_update.install(target, 'web', status, lambda _: None)
    assert status['native']['stage'] == 'verified'


def test_unsupported_requirement_is_rejected(tmp_path, monkeypatch):
    target = package(tmp_path, peerDependencies={'@deepseek-ai/dsh': '0.2.x'})
    runner(monkeypatch, target)
    with pytest.raises(ValueError, match='unsupported DSH requirement'):
        native_update.install(target, 'web', {}, lambda _: None)


def test_missing_requirement_is_rejected(tmp_path, monkeypatch):
    target = package(tmp_path, peerDependencies={})
    runner(monkeypatch, target)
    with pytest.raises(ValueError, match='no DSH version requirement'):
        native_update.install(target, 'web', {}, lambda _: None)
