"""DSH v4 transcript adapter tests (synthetic logs, no model/service)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    import zstandard
except ImportError:  # optional dev dependency; CI installs it
    zstandard = None

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = ROOT / "scripts" / "dsh_transcript.py"


def load_adapter():
    spec = importlib.util.spec_from_file_location("dsh_transcript", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_session(root: Path, session_id: str, events: list[dict], *, cwd: str = "/tmp/project") -> Path:
    path = root / "sessions" / "--tmp-project--" / session_id / "session.v4.jsonl.zstd"
    path.parent.mkdir(parents=True, exist_ok=True)
    compressor = zstandard.ZstdCompressor()
    with path.open("wb") as stream:
        for event in events:
            payload = dict(event)
            if payload.get("type") == "session" and "cwd" not in payload:
                payload["cwd"] = cwd
            stream.write(compressor.compress((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")))
    return path


def base_events(session_id: str, *, base_ms: int = 1_800_000_000_000, cwd: str = "/tmp/project") -> list[dict]:
    return [
        {"type": "session", "version": 4, "id": session_id, "createdAt": base_ms, "cwd": cwd},
        {"type": "turn/start", "seq": 1, "time": base_ms + 10, "data": {"turn": 1}},
        {
            "type": "user/message",
            "seq": 2,
            "time": base_ms + 20,
            "data": {
                "content": [{"type": "text", "text": "检查密钥 sk-test-abcdefghijklmnop 与 IP 10.1.2.3"}],
                "source": {"kind": "user"},
                "role": "user",
                "id": "u1",
            },
        },
        {
            "type": "assistant/message",
            "seq": 3,
            "time": base_ms + 30,
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
        {"type": "turn/end", "seq": 4, "time": base_ms + 40, "data": {"turn": 1, "reason": {"kind": "completed"}}},
        {
            "type": "user/message",
            "seq": 5,
            "time": base_ms + 50,
            "data": {
                "content": [{"type": "text", "text": "INJECTED-CONTEXT"}],
                "source": {"kind": "runtime-context"},
                "role": "user",
                "id": "u2",
            },
        },
    ]


class DshTranscriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if zstandard is None:
            raise unittest.SkipTest("zstandard is unavailable")
        cls.adapter = load_adapter()

    def test_public_text_excludes_reasoning_and_injected_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = "session-testone"
            path = write_session(root, session_id, base_events(session_id))
            out = self.adapter.read_material(str(path), 0, session_id=session_id, max_text_bytes=100_000)
            self.assertEqual(out["status"], "ok")
            self.assertTrue(out["text"].startswith("### user\n"))
            self.assertIn("公开结论", out["text"])
            self.assertNotIn("PRIVATE-REASONING", out["text"])
            self.assertNotIn("INJECTED-CONTEXT", out["text"])
            self.assertNotIn("tool-call", out["text"])
            self.assertFalse(out["more"])

    def test_incremental_pages_are_record_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = "session-pages"
            path = write_session(root, session_id, base_events(session_id))
            start = 0
            seen = []
            for _ in range(5):
                out = self.adapter.read_material(
                    str(path), start, session_id=session_id, max_text_bytes=80
                )
                self.assertEqual(out["status"], "ok")
                self.assertGreater(out["end"], start)
                seen.append(out["text"])
                start = out["end"]
                if not out["more"]:
                    break
            combined = "".join(seen)
            self.assertIn("检查密钥", combined)
            self.assertIn("公开结论", combined)

    def test_boundary_filters_before_activation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = "session-boundary"
            events = base_events(session_id)
            path = write_session(root, session_id, events)
            boundary = (1_800_000_000_000 + 25) / 1000
            out = self.adapter.read_material(
                str(path), 0, session_id=session_id, not_before=boundary, max_text_bytes=100_000
            )
            self.assertEqual(out["status"], "ok")
            self.assertNotIn("检查密钥", out["text"])
            self.assertIn("公开结论", out["text"])

    def test_wrong_task_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = write_session(root, "session-right", base_events("session-right"))
            out = self.adapter.read_material(str(path), 0, session_id="session-wrong")
            self.assertEqual(out["status"], "wrong-task")
            self.assertEqual(out["text"], "")

    def test_identity_survives_append(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            session_id = "session-append"
            path = write_session(root, session_id, base_events(session_id))
            first = self.adapter.read_material(str(path), 0, session_id=session_id, max_text_bytes=100_000)
            identity = self.adapter.identify(str(path))
            with path.open("ab") as stream:
                stream.write(zstandard.ZstdCompressor().compress((json.dumps(base_events(session_id)[2]) + "\n").encode()))
            out = self.adapter.read_material(
                str(path), first["end"], session_id=session_id, expected=identity, max_text_bytes=100_000
            )
            self.assertEqual(out["status"], "ok")
            self.assertFalse(out["more"])

    def test_short_identity_survives_repeated_append_across_anchor_limit(self) -> None:
        def event(text):
            return {"type": "assistant/message", "data": {
                "message": {"content": [{"type": "text", "text": text}]},
            }}

        with tempfile.TemporaryDirectory() as temporary:
            path = write_session(Path(temporary), "short", [event("first")])
            identity = self.adapter.identify(path)
            self.assertLess(identity.anchor_len, self.adapter.ANCHOR_BYTES)
            first = self.adapter.read_material(path, 0, session_id="short")
            cursor = first["end"]
            for text in ("second", "".join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(40))):
                with self.subTest(text_length=len(text)):
                    with path.open("ab") as stream:
                        stream.write(zstandard.ZstdCompressor().compress(
                            (json.dumps(event(text)) + "\n").encode()))
                    result = self.adapter.read_material(
                        path, cursor, session_id="short", expected=identity)
                    self.assertEqual(result["status"], "ok")
                    self.assertEqual(result["text"], "### assistant\n" + text)
                    self.assertGreater(result["end"], cursor)
                    cursor = result["end"]
            self.assertGreater(path.stat().st_size, self.adapter.ANCHOR_BYTES)
            self.assertEqual(self.adapter.read_material(
                path, cursor, session_id="short", expected=identity)["status"], "unchanged")

    def test_short_identity_rejects_rewritten_prefix_and_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = write_session(Path(temporary), "short", base_events("short")[:1])
            identity = self.adapter.identify(path)
            original = path.read_bytes()
            for payload in (original[:10], b"changed" + original[7:]):
                with self.subTest(size=len(payload)):
                    path.write_bytes(payload)
                    result = self.adapter.read_material(path, 0, expected=identity)
                    self.assertEqual(result["status"], "replaced")
                    self.assertEqual(result["text"], "")
                    self.assertEqual(result["end"], 0)

    def test_capture_source_and_identify_share_unified_spool_identity_and_verify_cwd(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            ws = root / "workspace"
            other_ws = root / "other-workspace"
            ws.mkdir(); other_ws.mkdir()
            session_id = "session-spool-cursor"
            highly_compressible = "Ascend NPU ACLGraph optimization step. " * 120
            events = base_events(session_id, cwd=str(ws))
            events[3]["data"]["message"]["content"][1]["text"] = highly_compressible
            path = write_session(root, session_id, events, cwd=str(ws))

            id_from_zstd = self.adapter.identify(path)
            assert id_from_zstd is not None
            self.assertGreater(id_from_zstd.size, path.stat().st_size)

            source_info = self.adapter.capture_source(
                path, session_id,
                binding={"session": session_id, "enabled": 1, "project_root": str(ws), "activated_at": 0.0},
            )
            spool_path = Path(source_info["transcript_path"])
            self.assertTrue(spool_path.is_file())
            id_from_spool = self.adapter.identify(spool_path)
            self.assertEqual(id_from_zstd, id_from_spool)

            # Reject binding whose project_root does not contain the native session cwd
            with self.assertRaises(ValueError):
                self.adapter.capture_source(
                    path, session_id,
                    binding={"session": session_id, "enabled": 1, "project_root": str(other_ws), "activated_at": 0.0},
                )

            # Passing spool_path + expected=id_from_zstd into read_material succeeds without "replaced"
            turn1 = self.adapter.read_increment(
                spool_path, 0, session_id=session_id, expected=id_from_zstd, scan_until=id_from_zstd.size,
            )
            self.assertEqual(turn1["status"], "ok")
            self.assertEqual(turn1["end"], id_from_zstd.size)
            ino1 = spool_path.stat().st_ino

            # Append Turn 2 to native .zstd and read directly via spool_path (refreshed via sidecar .meta.json)
            turn2_msg = {"type": "assistant/message", "seq": 10, "time": 1_800_000_001_000, "data": {
                "turn": 2, "step": 1, "message": {"role": "assistant", "content": [{"type": "text", "text": "Turn 2 conclusion " * 80}]},
            }}
            with path.open("ab") as stream:
                stream.write(zstandard.ZstdCompressor().compress((json.dumps(turn2_msg) + "\n").encode("utf-8")))

            id2 = self.adapter.identify(spool_path)
            assert id2 is not None
            self.assertGreater(id2.size, id_from_zstd.size)
            self.assertEqual(spool_path.stat().st_ino, ino1)
            unserialized = self.adapter.FileIdentity.unserialize(id_from_zstd.serialize(), str(path))
            self.assertEqual(unserialized.path, id2.path)
            turn2 = self.adapter.read_material(
                spool_path, turn1["end"], session_id=session_id, expected=unserialized, scan_until=id2.size,
            )
            self.assertEqual(turn2["status"], "ok")
            self.assertEqual(turn2["start"], turn1["end"])
            self.assertEqual(turn2["end"], id2.size)
            self.assertIn("Turn 2 conclusion", turn2["text"])

            from mindie_knowledge.loop import history
            planned = history.plan(str(path), session_id, root / "plan-out", parser=self.adapter)
            self.assertEqual(planned["status"], "complete")
            self.assertEqual(planned["cursor"], planned["snapshot_size"])
            self.assertEqual(planned["snapshot_size"], id2.size)

    def test_cold_spool_eviction_bounds_to_64_and_skips_locked_victim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state_dir = Path(os.environ.get("MINDIE_DSH_STATE_DIR") or (root / "state")).resolve()
            first_path = write_session(root, "session-evict-000", base_events("session-evict-000"))
            first_spool = self.adapter.project_spool(first_path)
            lock_path = first_spool.parent / f".{first_spool.stem}.lock"
            with self.adapter._spool_lock(lock_path):
                for idx in range(1, 70):
                    sid = f"session-evict-{idx:03d}"
                    p = write_session(root, sid, base_events(sid))
                    self.assertIsNotNone(self.adapter.identify(p))
                self.assertTrue(first_spool.is_file())
            spools = list((state_dir / "spools").glob("*.jsonl"))
            self.assertLessEqual(len(spools), 64)
            self.assertEqual(list((state_dir / "spools").glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
