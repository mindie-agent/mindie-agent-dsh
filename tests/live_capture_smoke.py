#!/usr/bin/env python3
"""Live platform smoke: DSH transcript -> redaction -> knowledge service.

This is intentionally not part of the offline unittest suite: it starts the
published knowledge service in a temporary domain and writes a real material
package. Run it with the pinned runtime:

    <venv>/python tests/live_capture_smoke.py

It prints one JSON receipt and exits nonzero on any unverified claim.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import zstandard  # noqa: E402
import dsh_transcript  # noqa: E402
from mindie_knowledge import consent_store  # noqa: E402
from mindie_knowledge.loop.activation import Admission  # noqa: E402
from mindie_knowledge.loop.cli import capture_hook, ensure_service, rpc  # noqa: E402
from mindie_knowledge.loop.handoff import prepare_schema  # noqa: E402
from mindie_knowledge.loop.settings import CommunityWriteContext  # noqa: E402


def redactor() -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "mindie_knowledge.loop.transcript_redaction"],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit("redactor install failed: " + (completed.stderr or completed.stdout)[-500:])
    value = completed.stdout.strip().splitlines()[-1].strip()
    if not Path(value).is_file():
        raise SystemExit("redactor path is missing: " + value)
    return value


def session_file(root: Path, session_id: str) -> Path:
    path = root / "sessions" / "--tmp-live-smoke--" / session_id / "session.v4.jsonl.zstd"
    path.parent.mkdir(parents=True)
    now = int(time.time() * 1000)
    events = [
        {"type": "session", "version": 4, "id": session_id, "createdAt": now, "cwd": str(root)},
        {"type": "turn/start", "seq": 1, "time": now + 1000, "data": {"turn": 1}},
        {
            "type": "user/message",
            "seq": 2,
            "time": now + 2000,
            "data": {
                "content": [{"type": "text", "text": "检查密钥 sk-live-smoke-abcdefghij 与 IP 10.9.8.7"}],
                "source": {"kind": "user"},
                "role": "user",
            },
        },
        {
            "type": "assistant/message",
            "seq": 3,
            "time": now + 3000,
            "data": {
                "turn": 1,
                "step": 1,
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "reasoning", "text": "PRIVATE-REASONING"},
                        {"type": "text", "text": "公开结论：先检查环境变量。"},
                    ],
                },
            },
        },
        {"type": "turn/end", "seq": 4, "time": now + 4000, "data": {"turn": 1, "reason": {"kind": "completed"}}},
    ]
    with path.open("wb") as stream:
        compressor = zstandard.ZstdCompressor()
        for event in events:
            stream.write(compressor.compress((json.dumps(event, ensure_ascii=False) + "\n").encode()))
    return path


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="mindie-dsh-capture-"))
    engine = None
    service = None
    receipt: dict = {"root": str(root)}
    try:
        state = root / "state"
        state.mkdir()
        adapter = root / "dsh_transcript.py"
        shutil.copyfile(SCRIPTS / "dsh_transcript.py", adapter)
        session_id = "session-" + secrets.token_hex(8)
        transcript = session_file(root, session_id)
        community = root / "community.json"
        consent = root / "consent.json"
        consent_store.record_choice(str(consent), "contribute")
        request = dict(
            enabled=True,
            repository="mindie-agent/knowledge-vllm-ascend",
            branch="main",
            project_roots=[str(root)],
            idle_seconds=300,
            visibility="public",
            account="live-smoke",
            consent_config=str(consent),
        )
        with CommunityWriteContext(str(community)) as context:
            context.configure(str(community), request)
        engine = root / "engine.json"
        engine.write_text(
            json.dumps(
                {
                    "root": str(state),
                    "domain": "vllm-ascend",
                    "admission_path": str(state / "admission.sqlite3"),
                    "capture_mode": "public-transcript",
                    "transcript_adapter": str(adapter),
                    "redactor_executable": redactor(),
                    "community_config": str(community),
                    "feeds": [],
                }
            )
        )
        prepare_schema(str(engine))
        admission = Admission(str(state / "admission.sqlite3"))
        lease = admission.activate(session_id, project_root=str(root))
        event = {
            "hook_event_name": "Stop",
            "stop_hook_active": False,
            "session_id": session_id,
            "identity_kind": "turn",
            "turn_id": "1",
            "transcript_path": str(transcript),
            "mindie_activation": lease["token"],
            "harness": "dsh",
            "cwd": str(root),
        }
        receipt["capture"] = capture_hook(str(engine), event)
        service = ensure_service(str(engine))
        deadline = time.time() + 90
        status = {}
        while time.time() < deadline:
            status = rpc(service, "status", {}, timeout=5)
            captures = status.get("captures") or []
            if captures and captures[0].get("status") in {"organized", "failed"}:
                break
            time.sleep(1)
        receipt["captures"] = status.get("captures")
        material = sorted((root / "state").rglob("blocks/*.md"))
        receipt["material_files"] = [str(path) for path in material]
        if not material:
            raise SystemExit("no material block was produced")
        body = material[0].read_text(encoding="utf-8")
        receipt["redacted_secret"] = "sk-live-smoke" not in body
        receipt["redacted_ip"] = "10.9.8.7" not in body
        receipt["public_only"] = "PRIVATE-REASONING" not in body and "公开结论" in body
        if not all(receipt[key] for key in ("redacted_secret", "redacted_ip", "public_only")):
            raise SystemExit("redaction/public projection assertion failed")
        print(json.dumps(receipt, ensure_ascii=False, indent=2))
        return 0
    finally:
        if service is not None:
            try:
                rpc(service, "stop", {}, timeout=5)
            except Exception:
                pass
        if engine is not None:
            try:
                subprocess.run(
                    [sys.executable, "-m", "mindie_knowledge.loop.cli", "stop", "--config", str(engine)],
                    capture_output=True,
                    timeout=30,
                )
            except Exception:
                pass
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
