"""DSH v4 concatenated-zstd public-transcript adapter for mindie-knowledge.
[Role] Projects `session.v4.jsonl.zstd` into a byte-addressed UTF-8 spool (`<state_dir>/spools/<hash>.jsonl`) and extracts public user/assistant text.
[Invariants] `identify()`, `FileIdentity`, `capture_source()`, `read_material()`, and `read_increment()` share one spool identity/byte space (`identify(zstd) == identify(spool)`), verify workspace `cwd`, and bound spools (`<= 64`).
[Timeouts & Locks] Decompression is bounded at 32 MiB / 5.0s, scan budget at 2.0s, per-spool lock wait at 5.0s, and cold eviction acquires victim `.lock` non-blockingly.
[Side-Effect Policy] Native DSH session logs are strictly read-only; maintains bounded uncompressed spool/meta files under `<state_dir>/spools/` with temp cleanup in `finally`.
"""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib, importlib, importlib.util, json, math, os, shutil, subprocess, time
from pathlib import Path

zstandard = importlib.import_module("zstandard") if importlib.util.find_spec("zstandard") is not None else None
ANCHOR_BYTES, MAX_DECOMPRESSED_BYTES, MAX_COLD_SPOOLS = 512, 32 * 1024 * 1024, 64
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
PUBLIC_USER_KINDS = {"user"}

def _strip_win_extended_prefix(path_str: str) -> str:
    if os.name != "nt": return path_str
    if path_str.startswith("\\\\?\\UNC\\"): return "\\\\" + path_str[8:]
    return path_str[4:] if path_str.startswith("\\\\?\\") else path_str

def _state_dir() -> Path:
    if explicit := os.environ.get("MINDIE_DSH_STATE_DIR"): return Path(explicit).expanduser()
    if data := (os.environ.get("MINDIE_DSH_DATA_HOME") or os.environ.get("MINDIE_DATA_HOME")): base = Path(data).expanduser()
    elif os.name == "nt": base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "mindie-agent"
    else: base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "mindie-agent"
    return base / "state" / "dsh"

def _is_zstd_path(path: str | Path) -> bool:
    name = Path(path).name.lower()
    return name.endswith(".zstd") or name.endswith(".zst")

def _spool_path_for_zstd(path: str | Path) -> Path:
    resolved = _strip_win_extended_prefix(str(Path(path).resolve()))
    spool_hash = hashlib.sha256(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:24]
    return (_state_dir() / "spools" / f"{spool_hash}.jsonl").resolve()

def _within_root(child: str | Path, root: str | Path) -> bool:
    try:
        c = os.path.normcase(_strip_win_extended_prefix(str(Path(child).resolve())))
        r = os.path.normcase(_strip_win_extended_prefix(str(Path(root).resolve())))
        return os.path.splitdrive(c)[0] == os.path.splitdrive(r)[0] and os.path.commonpath([c, r]) == r
    except (OSError, ValueError): return False

def _acquire_fd_nb(fd: int) -> bool:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            if os.fstat(fd).st_size == 0: os.write(fd, b"0"); os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except TimeoutError: raise
    except OSError: return False

def _release_fd(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt; os.lseek(fd, 0, os.SEEK_SET); msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl; fcntl.flock(fd, fcntl.LOCK_UN)
    except TimeoutError: raise
    except OSError: pass

@contextmanager
def _spool_lock(lock_path: Path, timeout: float = 5.0):
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, deadline, locked = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600), time.monotonic() + timeout, False
    try:
        while not (locked := _acquire_fd_nb(fd)):
            if time.monotonic() >= deadline: raise TimeoutError(f"timed out acquiring spool lock: {lock_path}")
            time.sleep(0.02)
        yield
    finally:
        if locked: _release_fd(fd)
        os.close(fd)

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
        return json.dumps({"dev": self.dev, "ino": self.ino, "anchor_len": self.anchor_len, "anchor_digest": self.anchor_digest}, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def unserialize(text, path):
        try:
            data = json.loads(text)
            anchor_len, anchor_digest = data["anchor_len"], data["anchor_digest"]
            if type(anchor_len) is not int or not 0 < anchor_len <= ANCHOR_BYTES or not isinstance(anchor_digest, str) or len(anchor_digest) != 64: return None
            clean = _strip_win_extended_prefix(str(path))
            if os.name != "nt" and len(clean) >= 3 and clean[1] == ":" and clean[2] in ("\\", "/"): norm_path = clean
            else:
                try:
                    norm_path = _strip_win_extended_prefix(str(_spool_path_for_zstd(clean) if _is_zstd_path(clean) else Path(clean).resolve()))
                except TimeoutError: raise
                except OSError: norm_path = clean
            return FileIdentity(norm_path, int(data["dev"]), int(data["ino"]), 0, 0, anchor_len, anchor_digest)
        except (ValueError, KeyError, TypeError): return None

def _seconds(raw):
    if type(raw) not in (int, float) or raw <= 0: return None
    value = float(raw)
    return value / 1000.0 if value >= 1e12 else value

def _read_json_dict(path: Path) -> dict | None:
    try: val = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
    except TimeoutError: raise
    except (OSError, ValueError): return None
    return val if isinstance(val, dict) else None

def _atomic_write_bytes(target: Path, payload: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try: tmp.write_bytes(payload); os.replace(tmp, target)
    finally: tmp.unlink(missing_ok=True)

def _decompress(path: Path) -> tuple[bytes, bool]:
    if zstandard is not None:
        chunks, total, partial = [], 0, False
        with open(path, "rb") as stream:
            reader = zstandard.ZstdDecompressor().stream_reader(stream, read_across_frames=True)
            while True:
                try: chunk = reader.read(1 << 20)
                except zstandard.ZstdError: partial = True; break
                if not chunk: break
                total += len(chunk)
                if total > MAX_DECOMPRESSED_BYTES:
                    chunks.append(chunk[:max(0, len(chunk) - (total - MAX_DECOMPRESSED_BYTES))]); partial = True; break
                chunks.append(chunk)
        return b"".join(chunks), partial
    zstd_bin = shutil.which("zstd")
    if not zstd_bin: raise RuntimeError("zstandard is unavailable in the pinned runtime")
    proc = subprocess.Popen([zstd_bin, "-dcq", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
    chunks, total, partial, deadline = [], 0, False, time.monotonic() + 5.0
    try:
        assert proc.stdout is not None
        while True:
            if time.monotonic() >= deadline: proc.kill(); raise TimeoutError("zstd decompression exceeded 5.0s budget")
            chunk = proc.stdout.read(1 << 20)
            if not chunk: break
            total += len(chunk)
            if total > MAX_DECOMPRESSED_BYTES:
                chunks.append(chunk[:max(0, len(chunk) - (total - MAX_DECOMPRESSED_BYTES))]); partial = True; proc.kill(); break
            chunks.append(chunk)
        if proc.wait(timeout=1.0) != 0: partial = True
    finally:
        if proc.poll() is None: proc.kill()
        if proc.stdout is not None: proc.stdout.close()
    return b"".join(chunks), partial

def _evict_cold_spools(spools_dir: Path, *, keep_stem: str) -> None:
    try: spools = [item for item in spools_dir.glob("*.jsonl") if item.is_file()]
    except TimeoutError: raise
    except OSError: return
    if len(spools) <= MAX_COLD_SPOOLS: return
    dated = []
    for item in spools:
        if item.stem == keep_stem: continue
        try: dated.append((item.stat().st_mtime_ns, item))
        except TimeoutError: raise
        except OSError: continue
    dated.sort(key=lambda pair: pair[0])
    removed, needed = 0, len(spools) - MAX_COLD_SPOOLS
    for _mtime, victim in dated:
        if removed >= needed: break
        lock_file = spools_dir / f".{victim.stem}.lock"
        try: fd = os.open(lock_file, os.O_RDWR | os.O_CREAT, 0o600)
        except TimeoutError: raise
        except OSError: continue
        try:
            if not _acquire_fd_nb(fd): continue
            try:
                victim.unlink(missing_ok=True)
                (spools_dir / f".{victim.stem}.meta.json").unlink(missing_ok=True)
                removed += 1
            finally:
                _release_fd(fd)
        except TimeoutError: raise
        except OSError: pass
        finally:
            os.close(fd)
            try: lock_file.unlink(missing_ok=True)
            except OSError: pass

def _prefix_matches(spool_path: Path, expected: bytes) -> bool:
    offset = 0
    with spool_path.open("rb") as handle:
        while offset < len(expected):
            chunk = handle.read(min(65536, len(expected) - offset))
            if not chunk or chunk != expected[offset:offset + len(chunk)]: return False
            offset += len(chunk)
    return True

def _sync_spool(path: str | Path) -> tuple[Path, dict]:
    resolved = _strip_win_extended_prefix(str(Path(path).resolve()))
    spool_hash = hashlib.sha256(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:24]
    spools_dir = (_state_dir() / "spools").resolve()
    spool_path, meta_path = spools_dir / f"{spool_hash}.jsonl", spools_dir / f".{spool_hash}.meta.json"
    with _spool_lock(spools_dir / f".{spool_hash}.lock"):
        with open(path, "rb") as src:
            stat = os.fstat(src.fileno())
            head = src.read(ANCHOR_BYTES)
            if stat.st_size > ANCHOR_BYTES: src.seek(max(0, stat.st_size - ANCHOR_BYTES)); tail = src.read(ANCHOR_BYTES)
            else: tail = head
        head_digest, tail_digest = hashlib.sha256(head).hexdigest(), hashlib.sha256(tail).hexdigest()
        cached = _read_json_dict(meta_path)
        if (cached is not None and spool_path.is_file() and cached.get("source_dev") == stat.st_dev and cached.get("source_ino") == stat.st_ino
                and cached.get("source_size") == stat.st_size and cached.get("source_mtime_ns") == stat.st_mtime_ns
                and cached.get("head_digest") == head_digest and cached.get("tail_digest") == tail_digest and spool_path.stat().st_size == cached.get("spool_size")):
            return spool_path, cached
        raw, partial = _decompress(Path(path))
        nl = raw.rfind(b"\n")
        committed = raw[:nl + 1] if nl >= 0 else b""
        if len(committed) < len(raw): partial = True
        if spool_path.is_file() and len(committed) >= (old_len := spool_path.stat().st_size) and _prefix_matches(spool_path, committed[:old_len]):
            if len(committed) > old_len:
                with spool_path.open("r+b") as out: out.seek(old_len); out.write(committed[old_len:]); out.flush()
        else:
            _atomic_write_bytes(spool_path, committed)
        resolved_obj = Path(resolved)
        owner = (resolved_obj.parent.name or None) if "sessions" in resolved_obj.parts else None
        meta = {"source_path": resolved, "source_dev": stat.st_dev, "source_ino": stat.st_ino, "source_size": stat.st_size, "source_mtime_ns": stat.st_mtime_ns,
                "head_digest": head_digest, "tail_digest": tail_digest, "spool_size": len(committed), "session_id": owner, "partial": partial}
        _atomic_write_bytes(meta_path, (json.dumps(meta, sort_keys=True) + "\n").encode("utf-8"))
        _evict_cold_spools(spools_dir, keep_stem=spool_hash)
        return spool_path, meta

def _resolve_spool(path: str | Path) -> tuple[Path, dict, str | None]:
    if _is_zstd_path(path):
        spool_path, meta = _sync_spool(path)
        return spool_path, meta, meta.get("session_id")
    resolved = Path(path).resolve()
    meta = _read_json_dict(resolved.parent / f".{resolved.stem}.meta.json") if resolved.suffix.lower() == ".jsonl" else None
    if isinstance(meta, dict) and isinstance(src := meta.get("source_path"), str) and Path(src).is_file():
        spool_path, fresh = _sync_spool(src)
        return spool_path, fresh, fresh.get("session_id")
    owner = (resolved.parent.name or None) if "sessions" in resolved.parts else (meta.get("session_id") if isinstance(meta, dict) else None)
    return resolved, (meta or {}), (owner if isinstance(owner, str) and owner else None)

def project_spool(path: str | Path) -> Path:
    return _resolve_spool(path)[0]

def capture_source(path, session_id, *, binding=None) -> dict:
    if not isinstance(session_id, str) or not session_id.strip(): raise ValueError("native session identity is missing")
    spool_path, _meta, owner = _resolve_spool(path)
    if owner != session_id: raise ValueError("capture transcript belongs to another task")
    if not spool_path.is_file(): raise ValueError("capture source must be a regular transcript file")
    created, native_cwd = None, None
    with spool_path.open("rb") as stream:
        for _ in range(32):
            if not (line := stream.readline(65536)): break
            try: rec = json.loads(line.decode("utf-8", "replace"))
            except ValueError: continue
            if isinstance(rec, dict) and rec.get("type") == "session":
                created = _seconds(rec.get("createdAt") or rec.get("time"))
                native_cwd = rec.get("cwd") if isinstance(rec.get("cwd"), str) and os.path.isabs(rec["cwd"]) else None
                break
    if not native_cwd or created is None: raise ValueError("native task scope or creation boundary is unavailable")
    project_root = str(Path(native_cwd).resolve())
    if binding is not None:
        scope, boundary = binding.get("project_root"), binding.get("activated_at")
        if binding.get("session") != session_id or binding.get("enabled") != 1 or not isinstance(scope, str) or not os.path.isabs(scope) or type(boundary) not in (int, float) or not math.isfinite(boundary) or boundary < 0:
            raise ValueError("native task binding is invalid")
        if not _within_root(native_cwd, scope):
            raise ValueError("native session cwd is outside the authorized project root")
        project_root = str(Path(scope).resolve())
    return {"session_id": session_id, "project_root": project_root, "created_at": created, "transcript_path": _strip_win_extended_prefix(str(spool_path.resolve()))}

def identify(path) -> FileIdentity | None:
    try:
        spool_path, _meta, _owner = _resolve_spool(path)
        with open(spool_path, "rb") as stream: stat, anchor = os.fstat(stream.fileno()), stream.read(ANCHOR_BYTES)
        resolved = _strip_win_extended_prefix(str(spool_path.resolve()))
    except TimeoutError: raise
    except (OSError, ValueError, RuntimeError): return None
    return FileIdentity(resolved, stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())

def _text_blocks(content) -> list[str]:
    if not isinstance(content, list): return []
    return [text for block in content if isinstance(block, dict) and block.get("type") == "text" and isinstance(text := block.get("text"), str) and text.strip()]

def _extract_public(event: dict, boundary: float | None) -> tuple[str, str] | None:
    kind, data, stamp = event.get("type"), (event.get("data") if isinstance(event.get("data"), dict) else {}), _seconds(event.get("time"))
    if boundary is not None and (stamp is None or stamp < boundary): return None
    if kind == "user/message":
        source = data.get("source") if isinstance(data.get("source"), dict) else {}
        if source.get("kind") not in PUBLIC_USER_KINDS: return None
        texts = _text_blocks(data.get("content"))
        return ("user", "\n".join(texts)) if texts else None
    if kind == "assistant/message":
        message = data.get("message") if isinstance(data.get("message"), dict) else {}
        texts = _text_blocks(message.get("content"))
        return ("assistant", "\n".join(texts)) if texts else None
    return None

def read_material(path, start, *, session_id=None, not_before=None, expected=None, max_scan_bytes=16777216, max_seconds=2.0, max_text_bytes=49152, scan_until=None):
    if type(start) is not int or start < 0: raise ValueError("start must be a nonnegative offset")
    if max_scan_bytes <= 0 or max_seconds <= 0 or max_text_bytes <= 0: raise ValueError("invalid scan budget")
    if scan_until is not None and (type(scan_until) is not int or scan_until < start): raise ValueError("scan_until must be an exact byte boundary at or after start")
    result = dict(status="ok", start=start, end=start, digest=hashlib.sha256(b"").hexdigest(), text="", records=0, skipped_records=0, oversize_records=0,
                  partial=False, more=False, timestamps_reliable=True, session_match=None, coverage_note=None, coverage=[])
    boundary = 0.0 if (type(not_before) in (int, float) and math.isfinite(not_before) and not_before == 0) else (_seconds(not_before) if not_before is not None else None)
    try:
        begun = time.monotonic()
        spool_path, meta, owner = _resolve_spool(path)
        result["partial"] = bool(meta.get("partial", False))
        resolved = _strip_win_extended_prefix(str(spool_path.resolve()))
        with open(spool_path, "rb") as raw_src:
            spool_stat = os.fstat(raw_src.fileno())
            head = raw_src.read(max(ANCHOR_BYTES, expected.anchor_len if expected is not None else 0))
        anchor, spool_size = head[:ANCHOR_BYTES], spool_stat.st_size
        current = FileIdentity(resolved, spool_stat.st_dev, spool_stat.st_ino, spool_size, spool_stat.st_mtime_ns, len(anchor), hashlib.sha256(anchor).hexdigest())
        result["identity"], result["snapshot_size"] = current.serialize(), spool_size
        if expected is not None and (expected.path != resolved or expected.dev != spool_stat.st_dev or expected.ino != spool_stat.st_ino
                                     or not expected.anchor_len or hashlib.sha256(head[:expected.anchor_len]).hexdigest() != expected.anchor_digest):
            result.update(status="replaced", coverage_note="transcript changed before read"); return result
        if owner:
            result["session_match"] = not session_id or owner == session_id
            if session_id and owner != session_id: result.update(status="wrong-task", coverage_note="transcript belongs to another task"); return result
        elif session_id:
            result.update(status="unknown-format", coverage_note="task path identity unavailable"); return result
        if start > spool_size: result.update(status="replaced", coverage_note="transcript truncated"); return result
        if start == spool_size:
            result.update(status="unchanged", end=start)
            if spool_size == 0 and not result["partial"]: result.update(status="unknown-format", coverage_note="no recognized DSH session records")
            return result
        consumed, included, recognized, text_size, end = hashlib.sha256(), [], 0, 0, start
        limit = min(spool_size, scan_until) if scan_until is not None else spool_size
        end_limit = min(limit, start + max_scan_bytes)
        with spool_path.open("rb") as stream:
            if start > 0:
                stream.seek(start - 1)
                if stream.read(1) != b"\n": result.update(status="invalid-boundary", coverage_note="cursor is not a record boundary"); return result
            stream.seek(start)
            while stream.tell() < end_limit:
                offset = stream.tell()
                raw_line = stream.readline(end_limit - offset)
                if not raw_line: break
                if not raw_line.endswith(b"\n"):
                    if offset + len(raw_line) == spool_size: result["partial"] = True
                    break
                next_offset = stream.tell()
                try:
                    parsed = json.loads(raw_line.decode("utf-8", "replace"))
                    event = parsed if isinstance(parsed, dict) else None
                except ValueError: event = None
                if event is not None:
                    etype = event.get("type")
                    if isinstance(etype, str) and (etype == "session" or "/" in etype): recognized += 1
                    extracted = _extract_public(event, boundary)
                    if extracted is not None and (norm := extracted[1].replace("\r\n", "\n").replace("\r", "\n").strip()):
                        rendered = f"### {extracted[0]}\n{norm}"
                        size = len(rendered.encode("utf-8")) + (2 if included else 0)
                        if included and text_size + size > max_text_bytes: break
                        if not included and size > max_text_bytes:
                            rendered = rendered.encode("utf-8")[:max_text_bytes].decode("utf-8", "ignore")
                            size = len(rendered.encode("utf-8")); result["oversize_records"] += 1
                        included.append(rendered); text_size += size
                    else: result["skipped_records"] += 1
                else: result["skipped_records"] += 1
                consumed.update(raw_line); end = next_offset
                if time.monotonic() - begun >= max_seconds: break
        result.update(end=end, text="\n\n".join(included), records=len(included), digest=consumed.hexdigest(), more=end < limit)
        if end == start: result["status"] = "unchanged"
        elif not recognized and not included: result.update(status="unknown-format", text="", coverage_note="no recognized DSH session records")
        return result
    except TimeoutError: raise
    except OSError: result.update(status="missing", coverage_note="transcript unreadable"); return result
    except RuntimeError as exc: result.update(status="unknown-format", coverage_note=str(exc)); return result

read_increment = read_material
