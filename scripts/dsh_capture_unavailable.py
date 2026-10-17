"""DSH public-transcript capture adapter.

MindIE Agent 0.0.1 for DSH does **not** wire a Session-Stop capture path. The
knowledge engine configuration nevertheless requires a transcript adapter, so
this module satisfies the startup contract and fails loudly if a future caller
attempts to use it without implementing DSH session parsing first.

It is intentionally not a silent empty parser: silently dropping a capture
would violate the product's "errors remain visible" principle.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

CAPTURE_MESSAGE = (
    "DSH public-transcript capture is not enabled in mindie-agent-dsh 0.0.1; "
    "no DSH session parser is installed"
)


class FileIdentity:
    """Minimal identity value required by the knowledge adapter contract."""

    def __init__(
        self,
        path: str,
        dev: int,
        ino: int,
        size: int,
        mtime_ns: int,
        anchor_len: int = 0,
        anchor_digest: str = "",
    ) -> None:
        self.path = path
        self.dev = dev
        self.ino = ino
        self.size = size
        self.mtime_ns = mtime_ns
        self.anchor_len = anchor_len
        self.anchor_digest = anchor_digest

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
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def deserialize(cls, path: str, text: str) -> "FileIdentity":
        data: Any = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("malformed transcript identity")
        return cls(
            path,
            int(data["dev"]),
            int(data["ino"]),
            -1,
            -1,
            int(data.get("anchor_len", 0)),
            str(data.get("anchor_digest", "")),
        )


def identify(path: str) -> FileIdentity | None:
    """Return None so discovery never claims a DSH session log.

    A caller that explicitly attempts a capture receives CAPTURE_MESSAGE from
    ``read_material`` below instead of a fabricated success.
    """

    if not isinstance(path, str) or not Path(path).is_file():
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    anchor = b""
    try:
        with open(path, "rb") as stream:
            anchor = stream.read(4096)
    except OSError:
        return None
    return FileIdentity(
        str(Path(path).resolve()),
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        len(anchor),
        hashlib.sha256(anchor).hexdigest(),
    )


def read_material(*_args: Any, **_kwargs: Any) -> None:
    raise NotImplementedError(CAPTURE_MESSAGE)
