"""DSH v4 transcript adapter tests (synthetic logs, no model/service)."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

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


def write_session(root: Path, session_id: str, events: list[dict]) -> Path:
    path = root / "sessions" / "--tmp-project--" / session_id / "session.v4.jsonl.zstd"
    path.parent.mkdir(parents=True, exist_ok=True)
    compressor = zstandard.ZstdCompressor()
    with path.open("wb") as stream:
        for event in events:
            stream.write(compressor.compress((json.dumps(event, ensure_ascii=False) + "\n").encode()))
    return path


def base_events(session_id: str, *, base_ms: int = 1_800_000_000_000) -> list[dict]:
    return [
        {"type": "session", "version": 4, "id": session_id, "createdAt": base_ms, "cwd": "/tmp/project"},
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
            # Activation after the first user message but before the assistant text.
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


if __name__ == "__main__":
    unittest.main()
