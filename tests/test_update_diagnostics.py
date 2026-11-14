"""DSH update wrappers retain typed causes and respect shared reporting policy."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import urllib.error

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import updater
import mindie_paths
from mindie_diagnostics.ingestion import ingest
from mindie_diagnostics.integration import configure_reporting
from mindie_diagnostics.outbox import Outbox


def _last_event(logs):
    files = sorted(Path(logs).glob('events/*/*.jsonl'), key=lambda path: path.stat().st_mtime)
    assert files, 'expected a local diagnostic event'
    return json.loads(files[-1].read_text(encoding='utf-8').splitlines()[-1])


def _record(error):
    policy = mindie_paths.diagnostics_config()
    logs = Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    assert updater.record_update_failure(
        error, SimpleNamespace(current_version='0.1.2', silent=True)) == 1
    return json.loads(updater.status_path().read_text()), _last_event(logs), logs


@pytest.mark.parametrize('cause,expected', [(ModuleNotFoundError('PRIVATE'), 1),
                                           (urllib.error.URLError('PRIVATE'), 0)])
def test_update_failure_receipt_and_queue_preserve_product_vs_transport(tmp_path, cause, expected):
    policy, logs = mindie_paths.diagnostics_config(), Path(os.environ['MINDIE_DIAGNOSTICS_ROOT'])
    configure_reporting(True, config=policy, roots=[str(logs)])
    error = updater.UpdateError('update failed')
    error.__cause__ = cause
    assert updater.record_update_failure(error, SimpleNamespace(current_version='0.1.2', silent=True)) == 1
    receipt = json.loads(updater.status_path().read_text())
    assert receipt['state'] == 'failed' and receipt['diagnostic']['incident_id']
    queue = Outbox(tmp_path / 'queue.db')
    try:
        assert ingest(logs, queue, config=policy)['enqueued'] == expected
    finally:
        if hasattr(queue, 'close'):
            queue.close()
    assert 'PRIVATE' not in ''.join(path.read_text(encoding='utf-8') for path in logs.glob('events/*/*.jsonl'))


def test_update_check_missing_adapter_source_is_a_named_prerequisite(tmp_path, monkeypatch):
    gone = tmp_path / 'mindie-agent-dsh-install'
    updater.atomic_json(updater.mp.config_file(), {
        'python': sys.executable,
        'package_dir': str(gone),
        'version': '0.1.2',
    })
    monkeypatch.setattr('native_update.current_version', lambda _profile: None)
    monkeypatch.setattr(updater, 'fetch_releases', lambda _api: [])
    with pytest.raises(FileNotFoundError, match='adapter source pyproject.toml is unavailable') as caught:
        updater.check('web', '0.1.2', 'https://example.invalid')
    assert str(gone) not in str(caught.value)
    receipt, event, logs = _record(caught.value)
    assert receipt['state'] == 'failed'
    assert receipt['detail'] == 'FileNotFoundError: adapter source pyproject.toml is unavailable'
    assert str(gone) not in receipt['detail']
    assert event['operation'] == 'update'
    assert event['attributes']['stage'] == 'update'
    assert event['attributes']['category'] == 'prerequisite'
    assert event['attributes']['error_code'] == 'update_prerequisite_unavailable'
    assert event['attributes']['error_type'] == 'FileNotFoundError'
    assert event['reportable'] is False
    assert str(gone) not in ''.join(path.read_text() for path in logs.glob('events/*/*.jsonl'))


def test_path_bearing_file_not_found_keeps_basename_and_prerequisite(tmp_path):
    missing = tmp_path / 'hidden' / 'pyproject.toml'
    with pytest.raises(FileNotFoundError) as caught:
        missing.read_text(encoding='utf-8')
    receipt, event, logs = _record(caught.value)
    assert receipt['detail'] == 'FileNotFoundError: pyproject.toml is unavailable'
    assert str(tmp_path) not in receipt['detail']
    assert event['attributes']['category'] == 'prerequisite'
    assert event['attributes']['error_code'] == 'update_prerequisite_unavailable'
    assert event['attributes']['error_type'] == 'FileNotFoundError'
    assert event['reportable'] is False
    assert str(tmp_path) not in ''.join(path.read_text() for path in logs.glob('events/*/*.jsonl'))


def test_missing_dsh_cli_message_stays_a_prerequisite():
    receipt, event, _logs = _record(FileNotFoundError('dsh is required to inspect its installed plugin'))
    assert 'dsh is required to inspect its installed plugin' in receipt['detail']
    assert event['attributes']['category'] == 'prerequisite'
    assert event['attributes']['error_code'] == 'update_prerequisite_unavailable'
    assert event['reportable'] is False


def test_unclassified_update_runtimeerror_remains_internal():
    _receipt, event, _logs = _record(RuntimeError('native helper dumped core'))
    assert event['attributes']['category'] == 'internal'
    assert event['attributes']['error_code'] == 'update_internal_failure'
    assert event['attributes']['error_type'] == 'RuntimeError'
    assert event['reportable'] is True
