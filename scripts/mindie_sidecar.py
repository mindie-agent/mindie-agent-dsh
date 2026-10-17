#!/usr/bin/env python3
"""DSH Host-plugin sidecar for MindIE knowledge and entry operations.

The JavaScript Host plugin keeps this process warm so a tool call does not pay
the knowledge import cost. Protocol: one JSON request per line on stdin and
one JSON response per line on stdout. Diagnostics go to stderr only.

This process owns no retry loop. A query/explain/feedback call is one service
RPC with the session's activation capability; a failed call is reported back
to the plugin, which returns it to the model.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402

MAX_LINE = 256 * 1024


class SidecarError(RuntimeError):
    pass


def adapter_config() -> dict[str, Any]:
    path = mp.config_file()
    if not path.is_file():
        raise SidecarError(
            "MindIE runtime is not initialized; run: " + mp.setup_command()
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SidecarError(f"cannot read runtime configuration: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise SidecarError("runtime configuration is not an object")
    for key in ("python", "engine_config", "state_dir", "venv_dir"):
        if not isinstance(data.get(key), str):
            raise SidecarError(f"runtime configuration is missing {key}")
    return data


def engine_path() -> str:
    path = adapter_config()["engine_config"]
    if not Path(path).is_file():
        raise SidecarError("runtime engine configuration is missing; re-run setup")
    return path


def community_path() -> Path:
    return mp.config_home() / "mindie-community.json"


def consent_path() -> Path:
    return mp.config_home() / "mindie-consent.json"


def admission():
    from mindie_knowledge.loop.activation import Admission

    return Admission(str(Path(mp.data_home()) / "admission.sqlite3"))


def prepare() -> None:
    from mindie_knowledge.loop.handoff import prepare_schema

    prepare_schema(engine_path())


def public_settings() -> dict[str, Any]:
    from mindie_knowledge.loop import settings as settings_mod
    from mindie_knowledge import consent_store

    engine = engine_path()
    try:
        data = json.loads(Path(engine).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    community = settings_mod.load(data.get("community_config"))
    consent = consent_store.read(str(consent_path()))
    return {
        "configured": community.state in {"enabled", "disabled"},
        "enabled": bool(community.enabled),
        "repository": community.repository,
        "project_roots": [str(item) for item in community.project_roots],
        "consent": consent.get("state"),
        "choice": consent.get("choice"),
        "reporting": consent.get("reporting"),
        "capture_block": community.capture_block_kind(),
    }


def ensure_default_sharing() -> None:
    """Create the explicit read-only default on first activation.

    A missing authority is a fault, not an opt-out. The first explicit
    /mindie-agent activation records the install-level ``later`` choice and a
    valid disabled community document, so later gates see a real revocation
    while the entry can still offer to enable contribution for this task.
    """

    from mindie_knowledge import consent_store
    from mindie_knowledge.loop.settings import CommunityWriteContext

    consent = consent_path()
    community = community_path()
    if consent.exists() or community.exists():
        return
    consent_store.record_choice(str(consent), "later")
    request = {
        "enabled": False,
        "repository": None,
        "project_roots": [],
        "branch": "main",
        "idle_seconds": 300,
        "visibility": "public",
        "consent_config": str(consent),
    }
    with CommunityWriteContext(str(community)) as context:
        context.configure(str(community), request)


def lease_view(session: str) -> dict[str, Any]:
    row = admission().inspect(session)
    return {
        "status": row.get("status"),
        "enabled": bool(row.get("enabled")),
        "project_root": row.get("project_root"),
    }


def op_activate(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("session")
    cwd = args.get("cwd")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise SidecarError("cwd must be an absolute path")
    prepare()
    lease = admission().activate(session, project_root=cwd)
    ensure_default_sharing()
    return {
        "session": lease["session"],
        "token": lease["token"],
        "activated_at": lease["activated_at"],
        "project_root": lease["project_root"],
        "sharing": public_settings(),
    }


def op_status(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("session")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    return {"lease": lease_view(session), "sharing": public_settings()}


def op_deactivate(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("session")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    disabled = admission().deactivate(session)
    return {"deactivated": bool(disabled), "sharing": public_settings()}


def op_configure(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop.settings import CommunityWriteContext

    choice = args.get("choice")
    if choice not in {"contribute", "disabled"}:
        raise SidecarError("choice must be contribute or disabled")
    cwd = args.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise SidecarError("cwd must be an absolute path")
    repository = args.get("repository")
    account = args.get("account")
    if choice == "contribute":
        if not isinstance(repository, str) or "/" not in repository:
            raise SidecarError("repository owner/repo is required for contribution")
        if not isinstance(account, str) or not account.strip():
            raise SidecarError("account is required for contribution")
        consent_store.record_choice(str(consent_path()), "contribute")
    else:
        consent_store.record_choice(str(consent_path()), "disabled")
    request: dict[str, Any] = {
        "enabled": choice == "contribute",
        "repository": repository if choice == "contribute" else None,
        "project_roots": [cwd] if choice == "contribute" else [],
        "branch": "main",
        "idle_seconds": 300,
        "visibility": "public",
        "account": account if choice == "contribute" else None,
        "consent_config": str(consent_path()),
    }
    path = community_path()
    with CommunityWriteContext(str(path)) as context:
        state = context.configure(str(path), request)
    return {"state": state.state, "sharing": public_settings()}


def op_reporting(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge import consent_store

    value = args.get("reporting")
    if value is None:
        return {"reporting": consent_store.read(str(consent_path())).get("reporting")}
    if value not in consent_store.REPORTING:
        raise SidecarError("reporting must be enabled, disabled or later")
    consent_store.record_reporting(str(consent_path()), value)
    return {"reporting": value}


def connection():
    from mindie_knowledge.loop.cli import ensure_service

    return ensure_service(engine_path())


def op_rpc(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge.loop.transport import rpc

    session = args.get("session")
    token = args.get("token")
    method = args.get("method")
    payload = args.get("arguments")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    if not isinstance(token, str) or not token:
        raise SidecarError("this task is not activated; invoke /mindie-agent first")
    if not isinstance(method, str) or method not in {"query", "explain", "feedback"}:
        raise SidecarError("unknown knowledge operation")
    if not isinstance(payload, dict):
        raise SidecarError("arguments must be an object")
    return rpc(
        connection(),
        method,
        dict(payload, _session_id=session, _activation=token),
        timeout=None,
    )


def op_sync(_args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge.loop.transport import rpc

    return {"feeds": rpc(connection(), "sync", {}, timeout=None)}


OPS = {
    "activate": op_activate,
    "status": op_status,
    "deactivate": op_deactivate,
    "configure": op_configure,
    "reporting": op_reporting,
    "rpc": op_rpc,
    "sync": op_sync,
}


def response(identifier: Any, *, ok: bool, result: Any = None, error: Exception | None = None) -> dict[str, Any]:
    if ok:
        return {"id": identifier, "ok": True, "result": result}
    code = getattr(error, "error_code", None)
    return {
        "id": identifier,
        "ok": False,
        "error": {
            "type": type(error).__name__ if error is not None else "Error",
            "message": str(error) if error is not None else "unknown error",
            **({"code": code} if isinstance(code, str) else {}),
        },
    }


def main() -> int:
    stdin = sys.stdin
    while True:
        line = stdin.readline(MAX_LINE + 1)
        if not line:
            return 0
        if len(line) > MAX_LINE:
            print(json.dumps(response(None, ok=False, error=SidecarError("request exceeds limit"))), flush=True)
            continue
        if not line.strip():
            continue
        identifier = None
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise SidecarError("request must be an object")
            identifier = message.get("id")
            op = message.get("op")
            args = message.get("args")
            if not isinstance(op, str) or op not in OPS:
                raise SidecarError("unknown operation")
            if not isinstance(args, dict):
                args = {}
            result = OPS[op](args)
            print(json.dumps(response(identifier, ok=True, result=result), ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps(response(identifier, ok=False, error=exc), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
