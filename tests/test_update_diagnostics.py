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
    assert ingest(logs, queue, config=policy)['enqueued'] == expected
    assert 'PRIVATE' not in ''.join(path.read_text() for path in logs.glob('events/*/*.jsonl'))
