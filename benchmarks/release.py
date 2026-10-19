"""[Role] Measure a repeatable public-input component workload for releases.
[Invariants] Assert business output before reporting latency; no productivity claim.
[Timeouts & Locks] Bounded iterations, no network or background services.
[Side-Effect Policy] Temporary synthetic files and an explicit result JSON only.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import dsh_transcript
import zstandard
session = "release-benchmark"
records = [{"type": "session", "version": 4, "id": session, "createdAt": 1800000000000, "cwd": "/work"}]
for i in range(100):
    records.append({"type": "assistant/message", "seq": i + 1, "time": 1800000000000 + i,
                    "data": {"turn": 1, "step": i, "message": {"role": "assistant", "content": [
                    {"type": "reasoning", "text": "HIDDEN_SENTINEL"},
                    {"type": "text", "text": "Public NPU mapping observation " + str(i)}]}}})
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "sessions" / "project" / session / "session.v4.jsonl.zstd"
    path.parent.mkdir(parents=True)
    raw = "".join(json.dumps(r) + "\n" for r in records).encode()
    path.write_bytes(zstandard.ZstdCompressor().compress(raw))
    iterations = 100
    started = time.perf_counter()
    for _ in range(iterations):
        result = dsh_transcript.read_material(str(path), 0, session_id=session)
    elapsed = time.perf_counter() - started
    assert result["status"] == "ok" and "observation 99" in result["text"]
    assert "HIDDEN_SENTINEL" not in result["text"]
METRIC = "public_100_message_parse_latency"

Path(os.environ["MINDIE_BENCH_RESULT_PATH"]).write_text(json.dumps({
    "status": "pass", "metrics": [{"name": METRIC, "unit": "us/op",
    "value": elapsed * 1_000_000 / iterations, "iterations": iterations}]
}) + "\n", encoding="utf-8")
