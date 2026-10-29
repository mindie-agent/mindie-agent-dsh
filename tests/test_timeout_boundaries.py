"""Timeouts reach the owning protocol boundary without changing authority."""

import io
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dsh_organizer
import knowledge_domain_routing as routing
import knowledge_mcp
import mindie_paths as paths
import mindie_sidecar
import updater


def timeout_read(monkeypatch, target):
    read_text = Path.read_text
    error = TimeoutError("metadata read exceeded its deadline")

    def read(path, *args, **kwargs):
        if path == target:
            raise error
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    return error


def test_sidecar_config_timeout_reports_deadline_without_activating(tmp_path, monkeypatch, capsys):
    config = paths.config_file()
    config.write_text("{}")
    timeout_read(monkeypatch, config)
    message = {"id": 7, "op": "activate", "args": {"session": "s1", "cwd": str(tmp_path)}}
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(message) + "\n"))

    assert mindie_sidecar.main() == 0

    response = json.loads(capsys.readouterr().out)
    assert response["id"] == 7 and response["ok"] is False
    assert response["error"]["type"] == "TimeoutError"
    assert "deadline" in response["error"]["message"]
    assert not (paths.data_home() / "admission.sqlite3").exists()
    assert config.read_bytes() == b"{}"


def test_mcp_reexec_does_not_treat_config_timeout_as_unconfigured(monkeypatch):
    config = paths.config_file()
    config.write_text("{}")
    error = timeout_read(monkeypatch, config)
    monkeypatch.setenv("MINDIE_DSH_NO_REEXEC", "0")

    with pytest.raises(TimeoutError) as caught:
        knowledge_mcp.maybe_reexec()

    assert caught.value is error
    assert config.read_bytes() == b"{}"


def test_organizer_identity_cannot_downgrade_timed_out_endpoint_to_unavailable(monkeypatch):
    endpoint = dsh_organizer.endpoint_path()
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    endpoint.write_text("{}")
    error = timeout_read(monkeypatch, endpoint)

    with pytest.raises(TimeoutError) as caught:
        dsh_organizer.identity()

    assert caught.value is error


def test_same_domain_routing_does_not_use_legacy_fallback_after_read_timeout(tmp_path, monkeypatch):
    engine = tmp_path / "engine.json"
    engine.write_text('{"domain":"vllm-ascend"}')
    error = timeout_read(monkeypatch, engine)
    monkeypatch.setattr(routing, "_selector", lambda: None)

    with pytest.raises(TimeoutError) as caught:
        routing.resolve_route(engine, ref="mindie://vllm-ascend/topic")

    assert caught.value is error


def test_knowledge_connection_timeout_retains_protocol_timeout_classification(monkeypatch):
    error = TimeoutError("service startup deadline")
    calls = []

    def start(engine):
        calls.append(engine)
        raise error

    monkeypatch.setattr(knowledge_mcp, "ensure_service", start)
    monkeypatch.setattr(
        knowledge_mcp, "dispatch",
        lambda *_: knowledge_mcp.knowledge_connection({"engine_config": "/engine.json"}),
    )
    response = knowledge_mcp.handle({
        "jsonrpc": "2.0", "id": 8, "method": "tools/call",
        "params": {"name": "knowledge_query", "arguments": {"query": "failure"}},
    })

    assert response["result"]["isError"] is True
    assert "TimeoutError" in response["result"]["content"][0]["text"]
    assert "exceeded its deadline" in response["result"]["content"][0]["text"]
    assert calls == [str(Path("/engine.json").resolve())]


def test_release_lookup_timeout_records_failed_receipt_without_staging(monkeypatch):
    calls = []

    def open_request(request, **options):
        calls.append((request.full_url, options["timeout"]))
        raise TimeoutError("release response deadline")

    monkeypatch.setattr(updater.urllib.request, "urlopen", open_request)
    monkeypatch.setattr(sys, "argv", [
        "updater.py", "check", "--silent", "--current-version", "0.1.9",
        "--api", "https://example.invalid/releases",
    ])

    assert updater.main() == 1

    receipt = json.loads(updater.status_path().read_text())
    assert receipt["state"] == "failed"
    assert receipt["detail"] == "TimeoutError: release response deadline"
    assert calls == [("https://example.invalid/releases", 30)]
    assert not (updater.update_dir() / "generations").exists()
    assert not updater.current_path().exists()


def test_asset_read_timeout_propagates_with_partial_file_and_no_retry(tmp_path, monkeypatch):
    error = TimeoutError("asset response deadline")
    reads = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, size):
            reads.append(size)
            if len(reads) == 1:
                return b"partial archive"
            raise error

    monkeypatch.setattr(updater.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    destination = tmp_path / "asset.tgz"

    with pytest.raises(TimeoutError) as caught:
        updater.download("https://example.invalid/asset.tgz", destination)

    assert caught.value is error
    assert reads == [1024 * 1024, 1024 * 1024]
    assert destination.read_bytes() == b"partial archive"
