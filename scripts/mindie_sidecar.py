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
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402

MAX_LINE = 256 * 1024
ADAPTER_VERSION = "0.0.2"


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
        "fork": community.raw.get("fork"),
        "account": community.raw.get("account"),
        "project_roots": [str(item) for item in community.project_roots],
        "consent": consent.get("state"),
        "choice": consent.get("choice"),
        "reporting": consent.get("reporting"),
        "capture_block": community.capture_block_kind(),
    }


def _with_heal(path: Path, fn):
    try:
        return fn()
    except Exception:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return fn()


def _configure_community(request: dict[str, Any]):
    from mindie_knowledge.loop.settings import CommunityWriteContext

    path = community_path()

    def _write():
        with CommunityWriteContext(str(path)) as context:
            return context.configure(str(path), request)

    return _with_heal(path, _write)


def ensure_default_sharing() -> None:
    """Create or heal the explicit read-only default on activation."""
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    consent = consent_path()
    community = community_path()
    if consent_store.read(str(consent)).get("state") != "ok":
        _with_heal(consent, lambda: consent_store.record_choice(str(consent), "later"))
    if settings_mod.load(str(community)).state not in {"enabled", "disabled"}:
        _configure_community(
            {
                "enabled": False,
                "repository": None,
                "project_roots": [],
                "branch": "main",
                "idle_seconds": 300,
                "visibility": "public",
                "consent_config": str(consent),
            }
        )


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


COMMUNITY_REPOSITORY = "mindie-agent/knowledge-vllm-ascend"


def _gh(*arguments: str) -> str:
    executable = shutil.which("gh")
    if executable is None:
        raise SidecarError(
            "GitHub CLI (gh) is required to fork and publish community contributions"
        )
    try:
        completed = subprocess.run(
            [executable, *arguments],
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise SidecarError(f"gh could not run: {type(exc).__name__}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip().splitlines()
        raise SidecarError("gh failed: " + (detail[-1][:300] if detail else "unknown error"))
    return completed.stdout.strip()


def _github_account() -> str:
    value = _gh("api", "user", "--jq", ".login").strip()
    if not value or "/" in value:
        raise SidecarError("GitHub CLI did not return a usable account; pass account explicitly")
    return value


def _existing_fork(repository: str, account: str) -> str | None:
    try:
        raw = _gh(
            "repo", "list", account, "--fork", "--limit", "200",
            "--json", "nameWithOwner,parent",
        )
        values = json.loads(raw)
    except (SidecarError, ValueError):
        return None
    if not isinstance(values, list):
        return None
    for item in values:
        if not isinstance(item, dict):
            continue
        parent = item.get("parent")
        if isinstance(parent, dict):
            parent_name = parent.get("nameWithOwner")
            if not isinstance(parent_name, str):
                owner = parent.get("owner")
                login = owner.get("login") if isinstance(owner, dict) else None
                name = parent.get("name")
                if isinstance(login, str) and isinstance(name, str):
                    parent_name = login + "/" + name
            if parent_name == repository:
                name = item.get("nameWithOwner")
                if isinstance(name, str) and name.count("/") == 1:
                    return name
    return None


def _ensure_fork(repository: str, fork: str | None, account: str) -> str:
    if fork and fork.split("/", 1)[0] != account:
        raise SidecarError("fork must belong to the public GitHub account")
    existing = _existing_fork(repository, account)
    if existing:
        return existing
    _gh("repo", "fork", repository, "--clone=false")
    existing = _existing_fork(repository, account)
    if existing:
        return existing
    if fork:
        try:
            _gh("repo", "view", fork, "--json", "nameWithOwner", "--jq", ".nameWithOwner")
            return fork
        except SidecarError:
            pass
    raise SidecarError("GitHub did not expose the fork after the fork request")


def op_configure(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge import consent_store

    choice = args.get("choice")
    if choice not in {"contribute", "disabled"}:
        raise SidecarError("choice must be contribute or disabled")
    cwd = args.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise SidecarError("cwd must be an absolute path")
    repository = args.get("repository")
    account = args.get("account")
    fork = None
    if choice == "contribute":
        repository = repository or COMMUNITY_REPOSITORY
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise SidecarError("repository must be owner/repo")
        account = account or _github_account()
        if not isinstance(account, str) or not account.strip() or "/" in account:
            raise SidecarError("account must be a public GitHub account name")
        fork = args.get("fork")
        if fork is not None and (not isinstance(fork, str) or fork.count("/") != 1):
            raise SidecarError("fork must be owner/repo")
        fork = _ensure_fork(repository, fork, account)
    consent = consent_path()
    _with_heal(consent, lambda: consent_store.record_choice(str(consent), choice))
    request: dict[str, Any] = {
        "enabled": choice == "contribute",
        "repository": repository if choice == "contribute" else None,
        "fork": fork,
        "project_roots": [cwd] if choice == "contribute" else [],
        "branch": "main",
        "idle_seconds": 300,
        "visibility": "public",
        "account": account if choice == "contribute" else None,
        "consent_config": str(consent),
    }
    state = _configure_community(request)
    return {"state": state.state, "sharing": public_settings()}


def op_reporting(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge import consent_store

    config = mp.config_home() / "diagnostics.json"
    value = args.get("reporting")
    try:
        from mindie_diagnostics.integration import configure_reporting, reporting_status

        if value is None:
            result = reporting_status(config=config)
        else:
            if value not in consent_store.REPORTING:
                raise SidecarError("reporting must be enabled, disabled or later")
            result = configure_reporting(
                value == "enabled",
                repository="mindie-agent/mindie-agent",
                config=config,
            )
            consent = consent_path()
            _with_heal(consent, lambda: consent_store.record_reporting(str(consent), value))
            if value == "enabled":
                try:
                    worker = subprocess.run(
                        [sys.executable, "-m", "mindie_diagnostics.cli", "reporting", "ensure",
                         "--config", str(config)],
                        capture_output=True,
                        text=True,
                        timeout=120,
                        check=False,
                    )
                    result = dict(result)
                    result["worker"] = {
                        "status": "ensured" if worker.returncode == 0 else "failed",
                        "detail": (worker.stderr or worker.stdout or "")[-500:],
                    }
                except (OSError, subprocess.SubprocessError) as exc:
                    result = dict(result)
                    result["worker"] = {"status": "failed", "detail": type(exc).__name__}
        return {"reporting": value, "diagnostics": result}
    except SidecarError:
        raise
    except Exception as exc:
        raise SidecarError(
            f"diagnostics reporting is unavailable: {type(exc).__name__}: {exc}"
        ) from exc


def connection():
    from mindie_knowledge.loop.cli import config_at, connection_path, ensure_service

    engine = engine_path()
    try:
        return ensure_service(engine)
    except Exception:
        connection_path(config_at(engine)).unlink(missing_ok=True)
        return ensure_service(engine)


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
        timeout=30.0,
    )


def op_sync(_args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge.loop.transport import rpc

    return {"feeds": rpc(connection(), "sync", {}, timeout=120.0)}


OPS = {
    "activate": op_activate,
    "status": op_status,
    "deactivate": op_deactivate,
    "configure": op_configure,
    "reporting": op_reporting,
    "rpc": op_rpc,
    "sync": op_sync,
}


def diagnostic(operation: str, error: Exception) -> dict[str, Any] | None:
    """Record one bounded local incident; reporting remains a separate opt-in."""
    try:
        from mindie_diagnostics.integration import record_failure

        receipt = record_failure(
            "mindie-agent-dsh",
            operation,
            stage="tool",
            category=type(error).__name__,
            exception=error,
            host="dsh",
            version=ADAPTER_VERSION,
            reportable=not isinstance(error, SidecarError),
        )
        if isinstance(receipt, dict) and receipt.get("incident_id"):
            return {
                "incident_id": receipt.get("incident_id"),
                "record_ref": receipt.get("record_ref"),
            }
    except Exception:
        pass
    return None


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
            envelope = response(identifier, ok=False, error=exc)
            projected = diagnostic(op, exc)
            if projected is not None:
                envelope["error"]["diagnostic"] = projected
            print(json.dumps(envelope, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
