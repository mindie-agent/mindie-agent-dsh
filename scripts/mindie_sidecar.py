#!/usr/bin/env python3
"""[Role] Long-lived JSON-line sidecar serving DSH Host-plugin entry, admission, and knowledge RPCs.
[Invariants] Processes one JSON request line (< 256 KiB) per stdout response line; owns no retry loop. Read-only knowledge operations (`query`, `explain`, `sync`) run out of the box without a pre-existing lease unless explicitly deactivated; mutating operations (`feedback`, `distill`) auto-provision a default session lease (`not_before=0.0`) on the selected domain's `admission_path`.
[Timeouts & Locks] Knowledge query/explain/feedback RPCs bounded at 30.0s, feed sync 120.0s plus 5s process cleanup, distill uses 2 redaction scans + identity + summary worker + 20s receipt/cleanup; gh CLI fork operations 90.0s, reporting worker ensure 75.0s; native configure outer 600s covers five 90s gh operations plus worker startup; registered-domain deactivation has a 6s total budget and the host allows 30s with cleanup.
[Side-Effect Policy] Mutates admission.sqlite3, target-domain admission.sqlite3, mindie-consent.json, mindie-community.json, and diagnostics.json only on activate/deactivate/configure/reporting or on-demand write-lease provisioning; the resident process holds a shared runtime lease.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402
import dsh_diagnostics as diagnostics  # noqa: E402
import knowledge_domain_routing as routing  # noqa: E402
import rsi_explore  # noqa: E402
import sidecar_github  # noqa: E402
from knowledge_sync import sync_feeds  # noqa: E402
import runtime_lease  # noqa: E402

MAX_LINE = 256 * 1024
COMMUNITY_REPOSITORY = "mindie-agent/knowledge-vllm-ascend"
_REPO_RE = sidecar_github.REPO_RE
DISTILL_INPUT_REQUIRED = {"kind", "outcome", "source_locator", "content", "evidence_refs"}
DISTILL_INPUT_OPTIONAL = {"repetitions"}
DOMAIN_DEACTIVATE_TIMEOUT = 6.0


class SidecarError(RuntimeError):
    pass


def adapter_config() -> dict[str, Any]:
    path = mp.config_file()
    if not path.is_file():
        raise SidecarError("MindIE runtime is not initialized; run: " + mp.setup_command())
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except TimeoutError:
        raise
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


def admission_file(engine: str | Path | None = None) -> Path:
    try:
        candidate = Path(engine) if engine is not None else mp.engine_config()
        if candidate.is_file():
            doc = json.loads(candidate.read_text(encoding="utf-8"))
            adm = doc.get("admission_path") if isinstance(doc, dict) else None
            if isinstance(adm, str) and adm:
                return Path(adm)
    except TimeoutError:
        raise
    except (OSError, ValueError):
        pass
    return Path(mp.data_home()) / "admission.sqlite3"


def admission(engine: str | Path | None = None):
    from mindie_knowledge.loop.activation import Admission
    return Admission(str(admission_file(engine)))


def prepare() -> None:
    from mindie_knowledge.loop.handoff import prepare_schema
    prepare_schema(engine_path())


def public_settings() -> dict[str, Any]:
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    if mp.config_file().is_file():
        engine = engine_path()
        try:
            data = json.loads(Path(engine).read_text(encoding="utf-8"))
        except TimeoutError:
            raise
        except (OSError, ValueError) as exc:
            raise SidecarError(f"cannot read engine configuration {engine}: {type(exc).__name__}: {exc}") from exc
        comm_cfg = data.get("community_config")
    else:
        comm_cfg = str(community_path())
    community = settings_mod.load(comm_cfg)
    consent = consent_store.read(str(consent_path()))
    return {
        "configured": community.state in {"enabled", "disabled"}, "enabled": bool(community.enabled),
        "repository": community.repository, "fork": community.raw.get("fork"),
        "account": community.raw.get("account"), "project_roots": [str(item) for item in community.project_roots],
        "consent": consent.get("state"), "choice": consent.get("choice"), "reporting": diagnostics.status(),
        "choice_revision": consent.get("choice_revision"), "capture_block": community.capture_block_kind(),
    }


def _configure_community(request: dict[str, Any]):
    from mindie_knowledge.loop.settings import CommunityWriteContext
    path = community_path()
    with CommunityWriteContext(str(path)) as context:
        return context.configure(str(path), request)


def ensure_default_sharing() -> None:
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    consent = consent_path()
    authority = consent_store.read(str(consent))
    if authority["state"] in {"corrupt", "unreadable"}:
        raise consent_store.ConsentError(authority["error"], state=authority["state"])
    if settings_mod.load(str(community_path())).state not in {"enabled", "disabled"}:
        _configure_community({
            "enabled": False, "repository": None, "project_roots": [], "branch": "main",
            "idle_seconds": 300, "visibility": "public", "consent_config": str(consent),
        })


def lease_view(session: str) -> dict[str, Any]:
    row = knowledge_lease(session)
    return {"enabled": row is not None, "project_root": row.get("project_root") if row else None}


def knowledge_lease(session: str, *, engine: str | Path | None = None) -> dict[str, Any] | None:
    from mindie_knowledge.loop.activation import AdmissionUnavailable
    db = admission_file(engine)
    if not db.is_file():
        return None
    try:
        return admission(engine).knowledge_lease(session)
    except AdmissionUnavailable as exc:
        raise SidecarError(f"admission check failed: {type(exc).__name__}: {exc}") from exc


def ensure_not_deactivated(session: str, *, engine: str | Path | None = None) -> None:
    from mindie_knowledge.loop.activation import Admission, AdmissionUnavailable
    seen: set[Path] = set()
    for db in (admission_file(engine), Path(mp.data_home()) / "admission.sqlite3"):
        resolved = db.resolve(strict=False)
        if resolved in seen or not db.is_file():
            continue
        seen.add(resolved)
        try:
            status = Admission(str(db)).inspect(session).get("status")
        except AdmissionUnavailable as exc:
            raise SidecarError(f"admission check failed: {type(exc).__name__}: {exc}") from exc
        if status == "inactive":
            raise SidecarError("this task is not activated; session was explicitly deactivated")


def verify_optional_token(session: str, token: Any) -> dict[str, Any] | None:
    ensure_not_deactivated(session)
    if token is None:
        return knowledge_lease(session)
    if not isinstance(token, str) or not token:
        raise SidecarError("this task is not activated; invalid session token")
    lease = knowledge_lease(session)
    if lease is None or lease.get("token") != token:
        raise SidecarError("this task is not activated; invalid session token")
    return lease


def ensure_default_lease(
    session: str, *, project_root: str | None = None, engine: str | Path | None = None
) -> dict[str, Any]:
    ensure_not_deactivated(session, engine=engine)
    resolved_root = (
        str(Path(project_root).resolve())
        if isinstance(project_root, str) and project_root
        else str(Path.cwd().resolve())
    )
    existing = knowledge_lease(session, engine=engine)
    if existing is not None:
        if project_root is None or existing.get("project_root") == resolved_root:
            return existing
        return admission(engine).activate(
            session,
            project_root=resolved_root,
            root_session=existing.get("root_session") or session,
            not_before=0.0,
        )
    if engine is None or Path(engine).resolve() == Path(engine_path()).resolve():
        prepare()
    db = admission_file(engine)
    db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return admission(engine).activate(session, project_root=resolved_root, root_session=session, not_before=0.0)


def _domain_activation_view(selected: dict[str, Any]) -> dict[str, Any]:
    lease = selected.get("lease") if isinstance(selected.get("lease"), dict) else {}
    # The sidecar-to-host result carries the target lease, but the host strips
    # its token before returning entry output. Routing resolves the target
    # lease from the target Admission; the default host token is untouched.
    view = {
        "domain": selected.get("domain"), "generation": selected.get("generation"),
        "activated_at": lease.get("activated_at"), "project_root": lease.get("project_root"),
        "root_session": lease.get("root_session"),
        "lease": {
            "session": lease.get("session"), "token": lease.get("token"),
            "enabled": bool(lease.get("enabled")), "activated_at": lease.get("activated_at"),
            "project_root": lease.get("project_root"), "root_session": lease.get("root_session"),
        },
    }
    if isinstance(selected.get("engine_config"), str):
        view["engine_config"] = selected["engine_config"]
    return view


def _activate_domain(
    domain: str, session: str, project_root: str, default_lease: dict[str, Any]
) -> dict[str, Any]:
    if not isinstance(domain, str) or not domain:
        raise SidecarError("domain must be a nonempty canonical name")
    try:
        from mindie_knowledge.loop.domain_routes import activate_selected_domain
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise SidecarError(
            "explicit domain activation is unavailable in the installed knowledge runtime; "
            "update the paired runtime"
        ) from exc
    root_session = default_lease.get("root_session")
    if not isinstance(root_session, str) or not root_session:
        root_session = session
    config = adapter_config()
    try:
        selected = activate_selected_domain(
            config["engine_config"],
            domain=domain,
            domain_configs=routing.adapter_domain_configs(config),
            session=session,
            project_root=project_root,
            # Derived from the verified native default lease, never caller args.
            root_session=root_session,
        )
    except (routing.DomainRoutingError, TypeError, ValueError) as exc:
        raise SidecarError(str(exc)) from exc
    return _domain_activation_view(selected)


def op_activate(args: dict[str, Any]) -> dict[str, Any]:
    session, cwd, domain = args.get("session"), args.get("cwd"), args.get("domain")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise SidecarError("cwd must be an absolute path")
    prepare()
    lease = admission().activate(session, project_root=cwd)
    ensure_default_sharing()
    reporting = diagnostics.activate()
    result = {
        "session": lease["session"],
        "token": lease["token"],
        "activated_at": lease["activated_at"],
        "project_root": lease["project_root"],
        "sharing": {**public_settings(), "reporting_activation": reporting},
    }
    if domain is not None:
        # Keep the default host lease/token above untouched. The target gets
        # its own lease from its own Admission; current routing resolves it.
        result["domain_activation"] = _activate_domain(domain, session, cwd, lease)
    return result


def op_status(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("session")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    return {"lease": lease_view(session), "sharing": public_settings()}


def op_resume(args: dict[str, Any]) -> dict[str, Any]:
    """Restore the host cache from existing authority without granting entry."""
    session = args.get("session")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    lease = knowledge_lease(session)
    if lease is None:
        return {"activated": False}
    return {**lease, "sharing": public_settings()}


def _registered_domain_deactivator():
    try:
        from mindie_knowledge.loop.domain_routes import deactivate_registered_domains
    except ModuleNotFoundError as exc:
        # Old installed cores lack this exact optional module. A missing
        # internal dependency is not old-core compatibility and must stay visible.
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        raise SidecarError(
            "registered-domain deactivation is unavailable in the installed knowledge runtime; "
            "update the paired runtime"
        ) from exc
    return deactivate_registered_domains


def _seed_missing_for_deactivate(session: str, cwd: Any) -> None:
    adm_db = admission_file()
    if not adm_db.is_absolute():
        return
    adm = admission()
    if not adm_db.is_file() or adm.inspect(session).get("status") == "missing":
        adm_db.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = str(Path(cwd).resolve()) if isinstance(cwd, str) and Path(cwd).is_absolute() else str(Path.cwd().resolve())
        adm.associate(session, project_root=root, not_before=0.0)


def op_deactivate(args: dict[str, Any]) -> dict[str, Any]:
    session, cwd = args.get("session"), args.get("cwd")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    config = adapter_config()
    try:
        domain_configs = routing.adapter_domain_configs(config)
    except routing.DomainRoutingError as exc:
        raise SidecarError(str(exc)) from exc
    if not domain_configs:
        _seed_missing_for_deactivate(session, cwd)
        return {"deactivated": bool(admission().deactivate(session)), "sharing": public_settings()}
    deactivate = _registered_domain_deactivator()
    _seed_missing_for_deactivate(session, cwd)
    try:
        result = deactivate(
            config["engine_config"],
            domain_configs=domain_configs,
            session=session,
            timeout=DOMAIN_DEACTIVATE_TIMEOUT,
        )
    except (TypeError, ValueError) as exc:
        raise SidecarError(str(exc)) from exc
    # Revocation never depends on capture consent. Status is diagnostic only;
    # its failure is visible but cannot turn a successful revocation into an
    # apparent host-side failure that retains SESSION_STATE.
    sharing, sharing_error = None, None
    try:
        sharing = public_settings()
    except TimeoutError:
        raise
    except Exception as exc:  # noqa: BLE001 - revocation already succeeded
        sharing_error = f"{type(exc).__name__}: {exc}"
    out = {
        "deactivated": result["status"] == "inactive" and bool(result.get("changed")),
        "status": result["status"],
        "changed": bool(result.get("changed")),
        "domains": result["domains"],
        "sharing": sharing,
        **({"sharing_error": sharing_error} if sharing_error is not None else {}),
    }
    return out


def _gh(*arguments: str) -> str:
    return sidecar_github.run_gh(
        *arguments, error_cls=SidecarError, os_mod=os, shutil_mod=shutil, subprocess_mod=subprocess
    )


def _github_account() -> str:
    return sidecar_github.github_account(gh_fn=_gh, error_cls=SidecarError)


def _existing_fork(repository: str, account: str, fork: str | None = None) -> str | None:
    return sidecar_github.existing_fork(repository, account, fork, gh_fn=_gh, error_cls=SidecarError)


def _ensure_fork(repository: str, fork: str | None, account: str) -> str:
    return sidecar_github.ensure_fork(
        repository, fork, account, gh_fn=_gh, existing_fork_fn=_existing_fork, error_cls=SidecarError
    )


def op_configure(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge import consent_store
    from mindie_knowledge.loop import settings as settings_mod

    choice = args.get("choice")
    if choice not in {"contribute", "disabled"}:
        raise SidecarError("choice must be contribute or disabled")
    cwd = args.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise SidecarError("cwd must be an absolute path")
    if (args.get("visibility") or "public") != "public":
        raise SidecarError("visibility must be public")
    repository, account, fork = args.get("repository"), args.get("account"), args.get("fork")
    if choice == "contribute":
        repository = repository or COMMUNITY_REPOSITORY
        if not isinstance(repository, str) or not _REPO_RE.fullmatch(repository):
            raise SidecarError("repository must be owner/repo")
        if fork is not None and (not isinstance(fork, str) or not _REPO_RE.fullmatch(fork)):
            raise SidecarError("fork must be owner/repo")
        if account is not None and (not isinstance(account, str) or not account.strip() or "/" in account):
            raise SidecarError("account must be a public GitHub account name")
        if account and fork and fork.split("/", 1)[0] != account:
            raise SidecarError("fork must belong to the public GitHub account")
        account = account or (fork.split("/", 1)[0] if isinstance(fork, str) else None) or _github_account()
        if not isinstance(account, str) or not account.strip() or "/" in account:
            raise SidecarError("account must be a public GitHub account name")
        fork = _ensure_fork(repository, fork, account)

    consent = consent_path()
    authority = consent_store.read(str(consent))
    if authority["state"] in {"corrupt", "unreadable"}:
        raise consent_store.ConsentError(authority["error"], state=authority["state"])
    current_comm = settings_mod.load(str(community_path()))
    if current_comm.state in {"corrupt", "unreadable"} and not current_comm.schema_ok:
        raise ValueError("existing community settings are damaged; bytes preserved")

    request = {
        "enabled": choice == "contribute",
        "repository": repository if choice == "contribute" else None,
        "fork": fork if choice == "contribute" else None,
        "project_roots": [cwd] if choice == "contribute" else [],
        "branch": "main",
        "idle_seconds": 300,
        "visibility": "public",
        "account": account if choice == "contribute" else None,
        "consent_config": str(consent),
    }
    if choice == "contribute":
        state = _configure_community(request)
        consent_store.record_choice(str(consent), choice)
    else:
        consent_store.record_choice(str(consent), choice)
        state = _configure_community(request)
    reporting = diagnostics.activate()
    return {"state": state.state, "sharing": {**public_settings(), "reporting_activation": reporting}}


def cmd_sharing_enable(
    *, project_root: str | Path | None = None, repository: str | None = None,
    account: str | None = None, fork: str | None = None, visibility: str = "public",
) -> dict[str, Any]:
    resolved_root = str(Path(project_root).expanduser().resolve() if project_root is not None else Path.cwd().resolve())
    if not Path(resolved_root).is_dir():
        raise SidecarError(f"project root does not exist: {resolved_root}")
    return op_configure({
        "choice": "contribute", "cwd": resolved_root,
        "repository": repository or COMMUNITY_REPOSITORY,
        "account": account, "fork": fork, "visibility": visibility,
    })


def cmd_sharing_disable(*, project_root: str | Path | None = None) -> dict[str, Any]:
    resolved_root = str(Path(project_root).expanduser().resolve() if project_root is not None else Path.cwd().resolve())
    return op_configure({"choice": "disabled", "cwd": resolved_root})


def op_reporting(args: dict[str, Any]) -> dict[str, Any]:
    if args.get("reporting") is not None:
        raise SidecarError("Use the contribution choice; independent reporting switches were removed")
    return diagnostics.status()


def connection(engine: str | None = None):
    from mindie_knowledge.loop.cli import ensure_service

    diagnostics.bind_environment()
    # The shared runtime owns recovery and the start lock. An unknown service
    # state must remain visible instead of discarding another process's endpoint.
    return ensure_service(engine or engine_path())


def _require_distill_runtime() -> None:
    try:
        from mindie_knowledge.graded import distill
    except TimeoutError:
        raise
    except Exception as exc:
        raise SidecarError(
            "the installed knowledge runtime has no C6 distillation support; update the paired runtime"
        ) from exc
    required = ("value_gate", "noop_decision", "build_prompt", "decision_schema", "validate_decision")
    if (
        getattr(distill, "REQUEST_SCHEMA", None) != "mindie-distill-request/1"
        or not all(callable(getattr(distill, name, None)) for name in required)
    ):
        raise SidecarError(
            "the installed knowledge runtime has no C6 distillation support; update the paired runtime"
        )


def _distill_arguments(payload: dict[str, Any]) -> None:
    if set(payload) - {"inputs", "task_state", "domain", "related_refs", "proposal"}:
        raise SidecarError("distill accepts only inputs, task_state, domain, related_refs and proposal")
    inputs = payload.get("inputs")
    if not isinstance(inputs, list) or not inputs:
        raise SidecarError("distill requires a nonempty inputs array")
    for item in inputs:
        if (
            not isinstance(item, dict)
            or not DISTILL_INPUT_REQUIRED <= set(item)
            or set(item) - DISTILL_INPUT_REQUIRED - DISTILL_INPUT_OPTIONAL
        ):
            raise SidecarError("distill inputs have unsupported or missing fields")
    if not isinstance(payload.get("task_state"), str) or not payload["task_state"]:
        raise SidecarError("distill requires an explicit task_state")
    related_refs = payload.get("related_refs")
    if related_refs is not None and (
        not isinstance(related_refs, list)
        or any(not isinstance(item, str) or not item.strip() for item in related_refs)
    ):
        raise SidecarError("distill `related_refs` must be an array of nonempty strings")
    if payload.get("proposal") is not None and not isinstance(payload.get("proposal"), dict):
        raise SidecarError("distill `proposal` must be an object")


def _distill_timeout() -> float:
    from mindie_knowledge.loop import summary_budget
    return summary_budget.distill_rpc_timeout()


def _explain_route_ref(payload: dict[str, Any]) -> str | None:
    """Route explain via ``ref`` or the first ``refs`` item; reject mixed authorities."""
    ref = payload.get("ref")
    if ref is not None and (not isinstance(ref, str) or not ref.strip()):
        raise SidecarError("explain `ref` must be nonempty text")
    raw_refs = payload.get("refs")
    if raw_refs is not None and (
        not isinstance(raw_refs, list)
        or any(not isinstance(item, str) or not item.strip() for item in raw_refs)
    ):
        raise SidecarError("explain `refs` must be an array of nonempty strings")
    refs = list(raw_refs or [])
    authorities: list[str] = []
    for item in ([ref] if ref is not None else []) + refs:
        domain = routing.reference_domain(item)
        if domain is not None and domain not in authorities:
            authorities.append(domain)
    if len(authorities) > 1:
        raise SidecarError("explain refs have conflicting mindie:// domain authorities: " + ", ".join(authorities))
    return ref if ref is not None else (refs[0] if refs else None)


def _route(payload: dict[str, Any]) -> routing.Route:
    domain, ref = payload.get("domain"), payload.get("ref")
    if domain is None and ref is None:
        return routing.default_route(engine_path())
    config = adapter_config()
    try:
        return routing.resolve_route(
            config["engine_config"],
            domain=domain,
            ref=ref,
            domain_configs=routing.adapter_domain_configs(config),
        )
    except routing.DomainRoutingError as exc:
        raise SidecarError(str(exc)) from exc


def _forwarded_arguments(
    payload: dict[str, Any],
    route: routing.Route,
    method: str,
    session: str,
    token: str | None,
    lease: dict[str, Any] | None,
) -> dict[str, Any]:
    forwarded = {
        key: value for key, value in payload.items()
        if key not in {"_session_id", "_activation", "_session_verified"}
    }
    forwarded.pop("domain", None)
    if route.forward_domain is not None:
        forwarded["domain"] = route.forward_domain
    forwarded["_session_id"] = session
    if method in {"feedback", "distill"}:
        forwarded["_activation"] = lease["token"] if lease is not None else token
    elif route.cross_domain or not token:
        forwarded["_session_verified"] = True
    else:
        forwarded["_activation"] = token
    return forwarded


def op_rpc(args: dict[str, Any]) -> dict[str, Any]:
    from mindie_knowledge.loop.transport import rpc

    session, token, cwd = args.get("session"), args.get("token"), args.get("cwd")
    method, payload = args.get("method"), args.get("arguments")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    verified_lease = verify_optional_token(session, token)
    if not isinstance(method, str) or method not in {"query", "explain", "feedback", "distill"}:
        raise SidecarError("unknown knowledge operation")
    if not isinstance(payload, dict):
        raise SidecarError("arguments must be an object")
    if method == "distill":
        _distill_arguments(payload)
        _require_distill_runtime()
        timeout = _distill_timeout()
    else:
        if method in {"query", "explain"}:
            try:
                payload = rsi_explore.forwarded(payload)
            except ValueError as exc:
                raise SidecarError(str(exc)) from exc
        timeout = 30.0
    if isinstance(token, str) and token and (
        payload.get("domain") is not None or payload.get("ref") is not None or payload.get("refs") is not None
    ):
        try:
            admission().check(session, token)
        except ValueError as exc:
            raise SidecarError("this task is not activated; invalid session token") from exc
        except TimeoutError:
            raise
        except Exception as exc:
            raise SidecarError(
                f"admission check failed: {type(exc).__name__}: {exc}. "
                "Do not re-activate until the admission store is readable."
            ) from exc
    route_payload = payload
    if method == "explain":
        route_ref = _explain_route_ref(payload)
        if payload.get("ref") is None and route_ref is not None:
            route_payload = {**payload, "ref": route_ref}
    route = _route(route_payload)
    lease = None
    if route.cross_domain:
        try:
            if method in {"feedback", "distill"}:
                lease = routing.target_lease(
                    route.selected_path,
                    session,
                    domain=route.forward_domain,
                    ensure=True,
                    project_root=cwd if isinstance(cwd, str) else None,
                )
            else:
                routing.ensure_target_not_deactivated(
                    route.selected_path, session, domain=route.forward_domain
                )
        except routing.DomainRoutingError as exc:
            raise SidecarError(str(exc)) from exc
    elif method in {"feedback", "distill"}:
        lease = (
            ensure_default_lease(session, project_root=cwd, engine=route.selected_path)
            if isinstance(cwd, str) and cwd
            else (verified_lease or ensure_default_lease(session, engine=route.selected_path))
        )
    else:
        ensure_not_deactivated(session, engine=route.selected_path)
    active_token = token if isinstance(token, str) and token else (lease["token"] if lease else None)
    arguments = _forwarded_arguments(payload, route, method, session, active_token, lease)
    result = rpc(connection(str(route.selected_path)), method, arguments, timeout=timeout)
    if method == "distill":
        from mindie_knowledge.graded.distill import result_view
        result, is_error = result_view(result)
        return {**result, "_tool_error": is_error}
    return result


def op_sync(args: dict[str, Any]) -> dict[str, Any]:
    session = args.get("session")
    if not isinstance(session, str) or not session:
        raise SidecarError("session is required")
    verify_optional_token(session, args.get("token"))
    route = _route({"domain": args.get("domain")})
    if route.cross_domain:
        try:
            routing.ensure_target_not_deactivated(
                route.selected_path, session, domain=route.forward_domain
            )
        except routing.DomainRoutingError as exc:
            raise SidecarError(str(exc)) from exc
    return sync_feeds(adapter_config()["python"], route.selected_path)


OPS = {
    "activate": op_activate,
    "resume": op_resume,
    "status": op_status,
    "deactivate": op_deactivate,
    "configure": op_configure,
    "reporting": op_reporting,
    "rpc": op_rpc,
    "sync": op_sync,
}


def diagnostic(operation: str, error: Exception) -> dict[str, Any]:
    return diagnostics.failure(operation, "tool", error, reportable=not isinstance(error, SidecarError))


def response(identifier: Any, *, ok: bool, result: Any = None, error: Exception | None = None) -> dict[str, Any]:
    if ok:
        return {"id": identifier, "ok": True, "result": result}
    code = getattr(error, "error_code", None)
    return {
        "id": identifier, "ok": False,
        "error": {
            "type": type(error).__name__ if error is not None else "Error",
            "message": str(error) if error is not None else "unknown error",
            **({"code": code} if isinstance(code, str) else {}),
        },
    }


def _run_cli(argv: list[str]) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="mindie_sidecar.py")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    enable_p = sub.add_parser("sharing-enable")
    for flag, default in (("--project-root", None), ("--fork", None), ("--account", None), ("--repository", COMMUNITY_REPOSITORY), ("--visibility", "public")):
        enable_p.add_argument(flag, default=default)
    sub.add_parser("sharing-disable").add_argument("--project-root", default=None)
    parsed = parser.parse_args(argv)
    result = (
        cmd_sharing_enable(project_root=parsed.project_root, repository=parsed.repository, account=parsed.account, fork=parsed.fork, visibility=parsed.visibility)
        if parsed.subcommand == "sharing-enable"
        else cmd_sharing_disable(project_root=parsed.project_root)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    for _stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            try:
                _stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    diagnostics.bind_environment()
    runtime_lease.hold_runtime_lease()
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] in {"sharing-enable", "sharing-disable"}:
        return _run_cli(raw_argv)
    stdin = sys.stdin
    while line := stdin.readline(MAX_LINE + 1):
        if len(line) > MAX_LINE:
            print(json.dumps(response(None, ok=False, error=SidecarError("request exceeds limit"))), flush=True)
            continue
        if not line.strip():
            continue
        identifier, op = None, "request"
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise SidecarError("request must be an object")
            identifier, op, args = message.get("id"), message.get("op"), message.get("args")
            if not isinstance(op, str) or op not in OPS:
                raise SidecarError("unknown operation")
            result = OPS[op](args if isinstance(args, dict) else {})
            print(json.dumps(response(identifier, ok=True, result=result), ensure_ascii=False), flush=True)
        except TimeoutError as exc:
            envelope = response(identifier, ok=False, error=exc)
            envelope["error"]["message"] = f"operation exceeded its deadline: {exc}"
            print(json.dumps(envelope, ensure_ascii=False), flush=True)
        except Exception as exc:  # noqa: BLE001
            envelope = response(identifier, ok=False, error=exc)
            if projected := diagnostic(str(op or "request"), exc):
                envelope["error"]["diagnostic"] = projected
            print(json.dumps(envelope, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
