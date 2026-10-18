"""DSH v4 session-log public-transcript adapter for mindie-knowledge.

DSH stores one session as concatenated zstd frames, each frame adding a batch
of JSONL events. Public material is the ordered pair of:

* ``user/message`` events whose ``data.source.kind`` is ``user`` (native user
  input, not approvals, runtime context, skill catalogs or injected jobs);
* assistant ``text`` content blocks (public progress and final answers), never
  reasoning, tool calls, tool results or stream chunks.

The adapter renders a deterministic transcript (``### user`` / ``### assistant``
records separated by blank lines) and exposes byte offsets into that rendered
stream. The stream is append-only for an append-only session log, so the
knowledge core's cursor remains valid across incremental reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

try:  # available in the pinned runtime (ReMe dependency)
    import zstandard
except ImportError:  # pragma: no cover - reported as unknown-format below
    zstandard = None

ANCHOR_BYTES = 512
MAX_DECOMPRESSED_BYTES = 128 * 1024 * 1024
PUBLIC_USER_KINDS = {"user"}


@dataclass(frozen=True)
class FileIdentity:
    path: str
    dev: int
    ino: int
    size: int
    mtime_ns: int
    anchor_len: int = 0
    anchor_digest: str = ""

    @property
    def key(self) -> str:
        return f"{self.path}|{self.dev}:{self.ino}"

    def serialize(self) -> str:
        return json.dumps(
            {
                "dev": self.dev,
                "ino": self.ino,
                "anchor_len": self.anchor_len,
                "anchor_digest": self.anchor_digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def unserialize(text, path):
        try:
            data = json.loads(text)
            anchor_len = data["anchor_len"]
            anchor_digest = data["anchor_digest"]
            if (
                type(anchor_len) is not int
                or not 0 < anchor_len <= ANCHOR_BYTES
                or not isinstance(anchor_digest, str)
                or len(anchor_digest) != 64
            ):
                return None
            return FileIdentity(
                path, int(data["dev"]), int(data["ino"]), 0, 0,
                anchor_len, anchor_digest,
            )
        except (ValueError, KeyError, TypeError):
            return None


def identify(path) -> FileIdentity | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except (OSError, ValueError):
        return None
    try:
        stat = os.fstat(fd)
        anchor = os.read(fd, ANCHOR_BYTES)
    except OSError:
        return None
    finally:
        os.close(fd)
    return FileIdentity(
        str(Path(path).resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        len(anchor),
        hashlib.sha256(anchor).hexdigest(),
    )


def _seconds(raw):
    if type(raw) not in (int, float) or raw <= 0:
        return None
    value = float(raw)
    return value / 1000.0 if value >= 1e12 else value


def _session_in_path(path):
    try:
        resolved = Path(path).resolve()
        parts = resolved.parts
    except OSError:
        return None
    if "sessions" not in parts:
        return None
    # <DSH_HOME>/sessions/<encoded-cwd>/<session-id>/session.v4.jsonl.zstd
    parent = resolved.parent.name
    return parent or None


def _decompress(path):
    """Return (bytes, partial). Concatenated frames are read in order."""
    if zstandard is None:
        raise RuntimeError("zstandard is unavailable in the pinned runtime")
    chunks = []
    total = 0
    partial = False
    with open(path, "rb") as stream:
        reader = zstandard.ZstdDecompressor().stream_reader(stream, read_across_frames=True)
        while True:
            try:
                chunk = reader.read(1 << 20)
            except zstandard.ZstdError:
                partial = True
                break
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DECOMPRESSED_BYTES:
                chunks.append(chunk[: max(0, len(chunk) - (total - MAX_DECOMPRESSED_BYTES))])
                partial = True
                break
            chunks.append(chunk)
    return b"".join(chunks), partial


def _text_blocks(content):
    if not isinstance(content, list):
        return []
    values = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                values.append(text)
    return values


def _records(raw, *, boundary):
    """Yield (timestamp, kind, text) for public records in log order."""
    current_turn = None
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        stamp = _seconds(event.get("time"))
        if kind == "turn/start":
            current_turn = data.get("turn")
            continue
        if kind == "user/message":
            source = data.get("source") if isinstance(data.get("source"), dict) else {}
            if source.get("kind") not in PUBLIC_USER_KINDS:
                continue
            texts = _text_blocks(data.get("content"))
            if not texts:
                continue
            text = "\n".join(texts)
            if boundary is not None and (stamp is None or stamp < boundary):
                continue
            yield stamp, "user", text, current_turn
            continue
        if kind == "assistant/message":
            message = data.get("message") if isinstance(data.get("message"), dict) else {}
            texts = _text_blocks(message.get("content"))
            if not texts:
                continue
            text = "\n".join(texts)
            if boundary is not None and (stamp is None or stamp < boundary):
                continue
            yield stamp, "assistant", text, data.get("turn", current_turn)


def read_material(
    path,
    start,
    *,
    session_id=None,
    not_before=None,
    expected=None,
    max_scan_bytes=16777216,
    max_seconds=2.0,
    max_text_bytes=49152,
    scan_until=None,
):
    if type(start) is not int or start < 0:
        raise ValueError("start must be a nonnegative offset")
    if max_scan_bytes <= 0 or max_seconds <= 0 or max_text_bytes <= 0:
        raise ValueError("invalid scan budget")
    if scan_until is not None and (type(scan_until) is not int or scan_until < start):
        raise ValueError("scan_until must be an exact byte boundary at or after start")

    result = dict(
        status="ok",
        start=start,
        end=start,
        digest=hashlib.sha256(b"").hexdigest(),
        text="",
        records=0,
        skipped_records=0,
        oversize_records=0,
        partial=False,
        more=False,
        timestamps_reliable=True,
        session_match=None,
        coverage_note=None,
        coverage=[],
    )
    boundary = _seconds(not_before) if not_before is not None else None
    try:
        with open(path, "rb") as stream:
            stat = os.fstat(stream.fileno())
            anchor = stream.read(ANCHOR_BYTES)
            current = FileIdentity(
                str(Path(path).resolve()),
                stat.st_dev,
                stat.st_ino,
                stat.st_size,
                stat.st_mtime_ns,
                len(anchor),
                hashlib.sha256(anchor).hexdigest(),
            )
        result["identity"] = current.serialize()
        result["snapshot_size"] = stat.st_size
        if expected is not None and (
            expected.path != current.path
            or expected.dev != stat.st_dev
            or expected.ino != stat.st_ino
            or not expected.anchor_len
            or current.anchor_digest != expected.anchor_digest
        ):
            result.update(status="replaced", coverage_note="transcript changed before read")
            return result
        owner = _session_in_path(path)
        if owner:
            result["session_match"] = not session_id or owner == session_id
            if session_id and owner != session_id:
                result.update(status="wrong-task", coverage_note="transcript belongs to another task")
                return result
        elif session_id:
            result.update(status="unknown-format", coverage_note="task path identity unavailable")
            return result

        begun = time.monotonic()
        raw, partial = _decompress(path)
        result["partial"] = partial
        records = list(_records(raw, boundary=boundary))
        values = []
        for _stamp, kind, text, _turn in records:
            normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if normalized:
                values.append(f"### {kind}\n{normalized}".encode("utf-8"))
        full = b""
        spans = []
        for index, value in enumerate(values):
            if index:
                full += b"\n\n"
            begin = len(full)
            full += value
            spans.append((begin, len(full)))

        def next_begin(index):
            return spans[index + 1][0] if index + 1 < len(spans) else len(full)

        if start > len(full):
            result.update(status="replaced", coverage_note="transcript truncated")
            return result
        if start == len(full):
            result.update(status="unchanged", end=start)
            if not values and not partial:
                result.update(status="unknown-format", coverage_note="no recognized DSH session records")
            return result
        effective_start = start
        begin_index = next((i for i, (begin, _finish) in enumerate(spans) if begin == effective_start), None)
        if begin_index is None and full[start : start + 2] == b"\n\n":
            # The previous page ended at EOF; a later append puts the separator
            # before the first new record. A cursor at that separator is valid.
            effective_start = start + 2
            begin_index = next((i for i, (begin, _finish) in enumerate(spans) if begin == effective_start), None)
        if begin_index is None:
            result.update(status="invalid-boundary", coverage_note="cursor is not a record boundary")
            return result

        selected = 0
        end = effective_start
        for index in range(begin_index, len(spans)):
            candidate = next_begin(index)
            if selected:
                if candidate - effective_start > max_text_bytes or candidate - effective_start > max_scan_bytes:
                    break
                if scan_until is not None and candidate > scan_until:
                    break
            if time.monotonic() - begun >= max_seconds:
                break
            selected += 1
            end = candidate
        if selected == 0:
            result.update(status="unchanged", end=start)
            return result
        text = full[effective_start:end].decode("utf-8", "replace")
        result.update(
            end=end,
            text=text,
            records=selected,
            digest=hashlib.sha256(full[effective_start:end]).hexdigest(),
            more=end < len(full),
            skipped_records=max(0, len(records) - selected),
        )
        return result
    except OSError:
        result.update(status="missing", coverage_note="transcript unreadable")
        return result
    except RuntimeError as exc:
        result.update(status="unknown-format", coverage_note=str(exc))
        return result
