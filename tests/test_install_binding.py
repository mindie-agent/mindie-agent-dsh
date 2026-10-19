"""A native adapter pointer changes only after the real old service is idle."""
from contextlib import closing
import json
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import install_binding
from mindie_knowledge.loop import cli, lifecycle, transport
from mindie_knowledge.loop.engine import Engine
from mindie_knowledge.loop.locks import StartLock
from mindie_knowledge.loop.store import Store


def bindings(tmp_path):
    engine = dict(root=str(tmp_path / 'data'), domain='vllm-ascend', redactor_executable=sys.executable,
                  transcript_adapter=str(Path(__file__).resolve().parents[1] / 'scripts/dsh_transcript.py'))
    old, new = tmp_path / 'old.engine.json', tmp_path / 'new.engine.json'
    old.write_text(json.dumps(engine))
    new.write_text(json.dumps({**engine, 'summary_command': [sys.executable, 'new-worker.py']}))
    adapter = tmp_path / 'config.json'
    previous = dict(engine_config=str(old), domain_configs={'vllm-ascend': str(old)})
    candidate = dict(engine_config=str(new), domain_configs={'vllm-ascend': str(new)})
    adapter.write_text(json.dumps(previous))
    return adapter, previous, candidate, engine


def test_default_owner_is_retired_before_single_pointer_publication(tmp_path):
    path, previous, candidate, engine = bindings(tmp_path)
    old = Path(previous['engine_config'])
    before = old.read_bytes()
    result = install_binding.activate(path, previous, candidate, timeout=3)
    assert result['status'] == 'active'
    assert json.loads(path.read_text()) == candidate
    assert old.read_bytes() == before
    assert lifecycle.inspect_retirement(old)['status'] == 'retired'
    assert lifecycle.inspect_retirement(candidate['engine_config']) is None


def test_busy_default_owner_keeps_entire_old_binding(tmp_path):
    path, previous, candidate, config = bindings(tmp_path)
    connection = cli.connection_path(config)
    with closing(Store(config['root'], config['domain'])) as store, StartLock(connection.with_name('consumer.lock')):
        worker = Engine(store)
        service = transport.Service(worker, connection_path=connection,
            config_fingerprint=lifecycle.config_fingerprint(previous['engine_config'], config))
        thread = threading.Thread(target=service.serve, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 3
            while not connection.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert worker.begin_work()
            result = install_binding.activate(path, previous, candidate, timeout=3)
            assert result['status'] == 'deferred'
            assert json.loads(path.read_text()) == previous
            assert lifecycle.inspect_retirement(previous['engine_config']) is None
            assert transport.rpc(service.connection, 'status')['worker_alive']
            worker.end_work()
        finally:
            service.close()
            thread.join(timeout=3)


def test_failed_pointer_publication_restores_old_owner_admission(tmp_path, monkeypatch):
    path, previous, candidate, _ = bindings(tmp_path)
    def fail(*args, **kwargs):
        raise OSError('pointer write failed')
    monkeypatch.setattr(install_binding, '_atomic_write_text', fail)
    result = install_binding.activate(path, previous, candidate, timeout=3)
    assert result['status'] == 'incomplete' and result['restoration']['status'] == 'restored'
    assert json.loads(path.read_text()) == previous
    assert lifecycle.inspect_retirement(previous['engine_config']) is None


def test_changed_profile_defers_before_retiring_current_owner(tmp_path):
    path, previous, candidate, _ = bindings(tmp_path)
    changed = {**previous, 'domain_configs': {**previous['domain_configs'], 'custom': '/custom.engine.json'}}
    path.write_text(json.dumps(changed))
    result = install_binding.activate(path, previous, candidate, timeout=3)
    assert result == dict(status='deferred', reason='installation_changed')
    assert json.loads(path.read_text()) == changed
    assert lifecycle.inspect_retirement(previous['engine_config']) is None
