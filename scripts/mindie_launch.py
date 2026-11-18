#!/usr/bin/env python3
"""[Role] Pure-stdlib cold-start launcher for DSH: verify the bundled seed, fast-path passive hooks, and delegate MCP/summary/capture/setup to the active runtime.
[Invariants] No third-party or SQLite work on passive hooks; `Stop` requires an enabled community tier and an in-scope absolute project root; seed verification is streaming SHA-256 plus size; a configured Python is preferred.
[Timeouts & Locks] At most one bounded 1 MiB stdin read on `Stop`; no local locks; capture hands off with a 6.0s shared budget.
[Side-Effect Policy] Writes `{}`, the atomic pending-update marker, one seed/runtime child, and the runtime's own consent-gated capture state.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, time
from pathlib import Path
HOST = "dsh"
PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path: sys.path.insert(0, str(HERE))
CREATE_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
MAX_STDIN = 1048577
PENDING_SCHEMA = "mindie-pending-update/1"
VERSION = json.loads((PLUGIN_ROOT / "package.json").read_text(encoding="utf-8"))["version"]
def _read_json(path: Path) -> dict | None:
    try: value = json.loads(Path(path).read_bytes().decode("utf-8-sig"))
    except TimeoutError: raise
    except (OSError, ValueError): return None
    return value if isinstance(value, dict) else None
def _cfg_home() -> Path:
    if value := os.environ.get("MINDIE_CONFIG_HOME"): return Path(value)
    return (Path(os.environ.get("APPDATA") or Path.home() / "AppData/Roaming") if os.name == "nt" else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")) / "mindie-agent"
def _data_home() -> Path:
    if value := (os.environ.get("MINDIE_DSH_DATA_HOME") or os.environ.get("MINDIE_DATA_HOME")): return Path(value).expanduser()
    return (Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") if os.name == "nt" else Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")) / "mindie-agent"
def _state_dir() -> Path: return Path(os.environ.get("MINDIE_DSH_STATE_DIR") or _data_home() / "state" / HOST)
def _config_path(explicit: str | None = None) -> Path:
    if explicit: return Path(explicit).expanduser().resolve()
    for name in ("MINDIE_AGENT_CONFIG", "MINDIE_DSH_CONFIG"):
        if value := os.environ.get(name): return Path(value).expanduser().resolve()
    canonical = _cfg_home() / "dsh.json"
    if canonical.is_file(): return canonical
    dsh_home = os.environ.get("MINDIE_DSH_CONFIG_HOME")
    legacy = Path(dsh_home) / "config.json" if dsh_home else _cfg_home() / "dsh" / "config.json"
    return legacy if legacy.is_file() else (legacy if dsh_home else canonical)
def _verify_seed(pyz: Path, lock: Path) -> dict:
    document = _read_json(lock)
    if not isinstance(document, dict) or document.get("schema") != "mindie-harness-seed/1": raise ValueError("invalid mindie-harness seed lock")
    digest, size = hashlib.sha256(), 0
    with Path(pyz).open("rb") as stream:
        while block := stream.read(65536): digest.update(block); size += len(block)
    if size != document.get("size_bytes") or digest.hexdigest() != document.get("sha256"): raise ValueError("mindie-harness-seed.pyz SHA-256 mismatch")
    return document
def _engine_path(cfg_path: Path, cfg: dict | None) -> Path:
    override = os.environ.get("MINDIE_DSH_ENGINE_CONFIG")
    if override: return Path(override)
    value = (cfg or {}).get("engine_config")
    return Path(value) if isinstance(value, str) and value else cfg_path.parent / "dsh.engine.json"
def _stop_roots(cfg_path: Path) -> list[str]:
    cfg = _read_json(cfg_path) or {}
    engine = _read_json(_engine_path(cfg_path, cfg)) or {}
    community = _read_json(Path(engine["community_config"]) if isinstance(engine.get("community_config"), str) and engine["community_config"] else cfg_path.parent / "mindie-community.json")
    if not isinstance(community, dict) or community.get("enabled") is not True: return []
    consent = _read_json(Path(community["consent_config"]) if isinstance(community.get("consent_config"), str) and community["consent_config"] else cfg_path.parent / "mindie-consent.json")
    if not isinstance(consent, dict) or consent.get("choice") not in {"contribute", "local-only"}: return []
    raw_roots = community.get("project_roots")
    if not isinstance(raw_roots, list) or not raw_roots: return []
    roots = [os.path.normcase(os.path.realpath(item)) for item in raw_roots if isinstance(item, str) and os.path.isabs(item)]
    return roots if len(roots) == len(raw_roots) else []
def _stop_event(raw: bytes, roots: list[str]) -> dict | None:
    if not 0 < len(raw) <= MAX_STDIN or not raw.strip(): return None
    try: event = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError): return None
    if not isinstance(event, dict): return None
    for key in ("cwd", "workspace_root"):
        value = event.get(key)
        if not isinstance(value, str) or not os.path.isabs(value): continue
        target = os.path.normcase(os.path.realpath(value))
        for root in roots:
            try:
                if os.path.commonpath([target, root]) == root: return event
            except ValueError: continue
    return None
def _quiet() -> int:
    sys.stdout.buffer.write(b"{}\n"); sys.stdout.buffer.flush(); return 0
def _pending() -> None:
    target = _state_dir() / "pending-update-check.json"; target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_name(f".pending-update-check.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps({"schema": PENDING_SCHEMA, "host": HOST, "reason": "session_start", "requested_at": time.time()}) + "\n", encoding="utf-8"); os.replace(temporary, target)
    finally: temporary.unlink(missing_ok=True)
def _norm(path: Path) -> Path: return path.parent.resolve() / path.name
def _active_python(cfg_path: Path) -> Path | None:
    value = (_read_json(cfg_path) or {}).get("python")
    return Path(value).expanduser() if isinstance(value, str) and value and Path(value).expanduser().is_file() else None
def _has_runtime() -> bool:
    try: import mindie_harness  # noqa: F401
    except ImportError: return False
    return True
def _exec_launcher(python: Path, cfg_path: Path, argv: list[str], raw: bytes | None) -> int:
    command = [str(python), "-I", "-X", "utf8", str(HERE / "mindie_launch.py"), "--config", str(cfg_path), *argv]
    if raw is None and os.name != "nt": os.execv(str(python), command)
    if raw is not None: return subprocess.run(command, input=raw, creationflags=CREATE_NO_WINDOW).returncode
    try: sys.stdin.fileno(); sys.stdout.fileno(); sys.stderr.fileno(); io_kw = {"stdin": sys.stdin, "stdout": sys.stdout, "stderr": sys.stderr}
    except Exception: io_kw = {}
    return subprocess.run(command, **io_kw, creationflags=CREATE_NO_WINDOW).returncode
def _bootstrap(argv: list[str], cfg_path: Path, event: dict | None = None, raw: bytes | None = None) -> int:
    python = _active_python(cfg_path)
    if python is not None and _norm(python) != _norm(Path(sys.executable)): return _exec_launcher(python, cfg_path, argv, raw)
    if _has_runtime(): return _run_in_runtime(argv, cfg_path, event)
    seed = PLUGIN_ROOT / "assets" / "mindie-harness-seed.pyz"
    _verify_seed(seed, PLUGIN_ROOT / "assets" / "seed-lock.json")
    # Provision without rest arguments so an authorized Stop payload is not consumed by the seed's passive gate.
    boot = subprocess.run([sys.executable, "-I", "-X", "utf8", str(seed), "--host", HOST, "--plugin-root", str(PLUGIN_ROOT), "--config", str(cfg_path), "--cli-module", "mindie_harness.cli"], creationflags=CREATE_NO_WINDOW)
    if boot.returncode: return boot.returncode
    python = _active_python(cfg_path)
    if python is not None and _norm(python) != _norm(Path(sys.executable)): return _exec_launcher(python, cfg_path, argv, raw)
    if _has_runtime(): return _run_in_runtime(argv, cfg_path, event)
    sys.stderr.write("MindIE runtime provisioning did not yield an active interpreter\n"); return 1
def _dsh_adapter(**kwargs):
    from mindie_harness.mcp.gateway import ManagedMCPAdapter
    class DSHManagedAdapter(ManagedMCPAdapter):
        def dispatch_tool(self, ctx, arguments, profile):
            from mindie_harness.mcp.knowledge_rpc import KNOWLEDGE_NAMES
            result = super().dispatch_tool(ctx, arguments, profile)
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if ctx.tool_name not in KNOWLEDGE_NAMES or not isinstance(structured, dict) or structured.get("reason") != "knowledge_unavailable": return result
            return _knowledge_fallback(ctx, arguments, profile)
    return DSHManagedAdapter("dsh", **kwargs)
def _knowledge_fallback(ctx, arguments: dict, profile) -> dict:
    import sqlite3
    from mindie_harness.mcp.gateway_dispatch import mcp_result
    from mindie_knowledge.graded.distill import result_view
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import distill_selected, ensure_service
    from mindie_knowledge.loop.transport import rpc
    cfg = _read_json(profile.config_path) or {}
    domains = cfg.get("domain_configs") if isinstance(cfg.get("domain_configs"), dict) else {}
    req_domain = arguments.get("domain") if isinstance(arguments, dict) else None
    engine = Path(domains[req_domain]) if isinstance(req_domain, str) and isinstance(domains.get(req_domain), str) else _engine_path(profile.config_path, cfg)
    body = {key: value for key, value in (arguments or {}).items() if key != "domain"}
    session, method = ctx.session_id, ctx.tool_name.removeprefix("knowledge_")
    admission = token = None
    if method in {"feedback", "distill"}:
        admission = Admission((_read_json(engine) or {}).get("admission_path") or profile.admission_db)
        state = admission.inspect(session)
        if state.get("status") == "inactive": return mcp_result("MindIE knowledge is deactivated for this session.", is_error=True, structured={"reason": "knowledge_inactive"})
        lease = state if state.get("status") == "active" else admission.activate(session, project_root=str(ctx.cwd or Path.cwd()), root_session=session, not_before=0.0)
        token = lease.get("token")
    result, succeeded = None, False
    try:
        if method == "distill":
            payload = {"inputs": body.get("inputs"), "task_state": body.get("task_state"), "_session_id": session, "_activation": token}
            payload.update({key: body[key] for key in ("related_refs", "proposal", "topic", "target_signature") if key in body})
            view, is_error = result_view(distill_selected(str(engine), payload))
            result = mcp_result(json.dumps(view, ensure_ascii=False, sort_keys=True, default=str), is_error=is_error, structured=view)
            succeeded = not is_error
            return result
        raw = rpc(ensure_service(str(engine)), method, dict(body, _session_id=session, _session_verified=True), timeout=110.0 if method == "sync" else 35.0)
        view = {"status": "ok", "feeds": raw} if method == "sync" and isinstance(raw, list) else raw
        result = mcp_result(json.dumps(view, ensure_ascii=False, sort_keys=True, default=str), is_error=False, structured=view if isinstance(view, dict) else {"result": view})
        succeeded = True
        return result
    finally:
        pending = sys.exc_info()[1]
        if admission is not None and token and not isinstance(pending, (TimeoutError, ConnectionError)):
            try: admission.finish(session, token, succeeded)
            except (TimeoutError, Exception) as exc:
                if result is not None and not result.get("isError"):
                    structured = dict(result.get("structuredContent") or {})
                    structured.update(receipt_warning=f"{type(exc).__name__}: {exc}", receipt_unconfirmed=True)
                    result["structuredContent"] = structured
                    result["content"] = [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, sort_keys=True, default=str)}]
def _stop_runtime(profile, event: dict) -> int:
    from mindie_harness.sharing import sharing_status
    from mindie_knowledge import state_layout
    from mindie_knowledge.loop.activation import Admission
    from mindie_knowledge.loop.cli import capture_hook
    from mindie_knowledge.loop.handoff import prepare_schema
    cwd, session = event.get("cwd"), event.get("session_id")
    if not isinstance(cwd, str) or not os.path.isabs(cwd) or not isinstance(session, str) or not session: return _quiet()
    status = sharing_status(profile, cwd=cwd)
    if status.get("tier") not in {"contribute", "local-only"} or not status.get("cwd_in_scope"): return _quiet()
    cfg = _read_json(profile.config_path) or {}
    engine = _engine_path(profile.config_path, cfg)
    engine_doc = _read_json(engine) or {}
    admission = Admission(engine_doc.get("admission_path") or profile.admission_db)
    if admission.inspect(session).get("status") == "inactive": return _quiet()
    lease = admission.activate(session, project_root=cwd, root_session=session, not_before=0.0)
    event = {**event, "cwd": cwd, "mindie_activation": lease["token"]}
    root, domain = engine_doc.get("root"), engine_doc.get("domain")
    if isinstance(root, str) and root and isinstance(domain, str) and domain and not (state_layout.state_root(root, domain) / state_layout.DATABASE).is_file(): prepare_schema(str(engine))
    capture_hook(str(engine), event, timeout=6.0)
    return _quiet()
def _endpoint() -> dict | None:
    value = _read_json(_state_dir() / "summary-endpoint.json")
    if not isinstance(value, dict) or not isinstance(value.get("url"), str) or not value["url"].startswith("http://127.0.0.1:"): return None
    return value if all(isinstance(value.get(key), str) and value[key] for key in ("token", "model", "provider")) else None
def _identity(endpoint: dict | None) -> dict:
    from mindie_knowledge.materials.summarizer import digest, policy_identity
    model = endpoint.get("model") if isinstance(endpoint, dict) else None
    provider = endpoint.get("provider") if isinstance(endpoint, dict) else None
    if not isinstance(model, str) or not model or not isinstance(provider, str) or not provider: model, provider = "dsh/unavailable", "unavailable"
    value = policy_identity(model=model, effort=None, implementation={"provider": provider, "adapter": "dsh-llm"})
    value["capabilities"] = ["mindie-distill-request/1"]
    value["fingerprint"] = digest({key: item for key, item in value.items() if key != "fingerprint"})
    return value
def _post(endpoint: dict, payload: dict) -> tuple[str | None, dict | None, str | None]:
    import http.client, urllib.error, urllib.request
    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl): return None
    request = urllib.request.Request(endpoint["url"], data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + endpoint["token"]}, method="POST")
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(request, timeout=315) as response: raw = response.read(262145)
    except urllib.error.HTTPError as exc: return None, None, ("unknown" if exc.code >= 500 else "configuration")
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException): return None, None, "unknown"
    try: value = json.loads(raw)
    except ValueError: return None, None, "invalid_result"
    if not isinstance(value, dict) or not isinstance(value.get("text"), str): return None, None, "invalid_result"
    return value["text"], value.get("usage") if isinstance(value.get("usage"), dict) else None, None
def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()[1:]
        if lines and lines[-1].strip() == "```": lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start: raise ValueError("model returned no JSON object")
    value = json.loads(stripped[start:end + 1])
    if not isinstance(value, dict): raise ValueError("model returned a non-object")
    if "blocks" not in value and isinstance(value.get("block_indexes"), list): value["blocks"] = value.pop("block_indexes")
    if isinstance(value.get("navigation"), str):
        navigation = value["navigation"].strip()
        value["navigation"] = {"title": (navigation.splitlines()[0].strip() if navigation else "Task navigation")[:120], "summary": navigation[:1000]}
    return value
def _usage(raw: dict | None) -> dict | None:
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    return None if not isinstance(raw, dict) or any(type(raw.get(key)) is not int or raw[key] < 0 for key in keys) else {key: raw[key] for key in keys}
def _summary(profile, identity_only: bool) -> int:
    from mindie_knowledge.materials import summarizer
    endpoint, started = _endpoint(), time.monotonic()
    if identity_only: print(json.dumps(_identity(endpoint), ensure_ascii=True)); return 0
    def elapsed() -> int: return int((time.monotonic() - started) * 1000)
    def emit(request: dict, **values) -> int:
        try: envelope = summarizer.outcome(request, elapsed_ms=elapsed(), **values)
        except (KeyError, TypeError, ValueError): return 1
        print(json.dumps(envelope, ensure_ascii=True)); return 0
    try: request = json.loads(sys.stdin.buffer.read(MAX_STDIN).decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError): return 1
    if not isinstance(request, dict): return 1
    if endpoint is None: return emit(request, status="failed", model_calls=0, error="configuration", error_reason="configuration_invalid", billing_status="not_called")
    distill = request.get("schema_version") == "mindie-distill-request/1"
    if distill:
        from mindie_knowledge.graded import distill as graded
        try: request = graded.validate_request(request)
        except (KeyError, TypeError, ValueError): print(json.dumps({"schema": "mindie-summary-error/1", "error": "invalid_distill_request"}, ensure_ascii=True)); return 1
        reason = graded.value_gate(request)
        if reason: return emit(request, status="returned", result=graded.noop_decision(request, reason), model_calls=0, billing_status="not_called")
        identities = [_identity(endpoint)]; routes = endpoint.get("routes")
        if isinstance(routes, dict): identities.extend(_identity(item) for item in routes.values())
        if request["policy_identity"] not in identities: return emit(request, status="failed", model_calls=0, error="configuration", error_reason="worker_policy_changed", billing_status="not_called")
        payload = {"schema": "mindie-distill-request/1", "request": request, "prompt": graded.build_prompt(request), "output_schema": graded.decision_schema()}
    else:
        try: request = summarizer.validate_request(request)
        except (KeyError, TypeError, ValueError): print(json.dumps({"schema": "mindie-summary-error/1", "error": "invalid_summary_request"}, ensure_ascii=True)); return 1
        payload = {"schema": "mindie-summary-request/1", "request": request, "prompt": summarizer.PROMPT}
    text, raw_usage, failure = _post(endpoint, payload)
    usage = _usage(raw_usage)
    if failure == "configuration": return emit(request, status="failed", model_calls=0, error="configuration", error_reason="configuration_invalid", billing_status="not_called")
    if failure == "unknown": return emit(request, status="outcome_unknown", model_calls=1, error="unknown", error_reason="native_stream_disconnected", billing_status="unknown")
    if failure == "invalid_result": return emit(request, status="failed", usage=usage, usage_known=usage is not None, model_calls=1, error="invalid_result", error_reason="result_contract_invalid")
    try:
        value = _extract_json(text or "")
        result = graded.validate_decision(value, request) if distill else summarizer.validate_result(value, [block["block_id"] for block in request["blocks"]])
    except (KeyError, TypeError, ValueError, AssertionError):
        return emit(request, status="failed", raw_result=text, usage=usage, usage_known=usage is not None, model_calls=1, error="invalid_result", error_reason="result_contract_invalid")
    return emit(request, status="returned", result=result, raw_result=text, usage=usage, usage_known=usage is not None, model_calls=1)
def _run_in_runtime(argv: list[str], cfg_path: Path, event: dict | None = None) -> int:
    from mindie_harness.api import serve_mcp
    from mindie_harness.mcp.gateway import ManagedMCPAdapter, rebind_native_plugin_if_needed  # noqa: F401
    from mindie_harness.paths import ProfileRef
    profile = ProfileRef.resolve(HOST, plugin_root=PLUGIN_ROOT, config_path=cfg_path)
    os.environ.setdefault("REMOTE_DEV_STATE_DIR", str(profile.state_dir / "remote"))
    os.environ.setdefault("REMOTE_DEV_HARNESS_PROFILE", HOST)
    rebind_native_plugin_if_needed(profile, native_version=VERSION, runtime_scripts=HERE)
    if argv[:1] == ["mcp"]: return serve_mcp(argv[1] if len(argv) > 1 else "all", profile=profile, adapter=_dsh_adapter(plugin_root=PLUGIN_ROOT, config_path=cfg_path), managed=True)
    if argv[:2] == ["hook", "stop"]: return _stop_runtime(profile, event or {})
    if argv[:1] == ["summary"]: return _summary(profile, "--identity" in argv)
    command = ["init", *argv[1:]] if argv[:1] == ["setup"] else argv
    return subprocess.run([sys.executable, "-I", "-X", "utf8", str(HERE / "setup.py"), "--config", str(cfg_path), *command], creationflags=CREATE_NO_WINDOW).returncode
def main() -> int:
    argv, explicit, index, raw_args = [], None, 0, sys.argv[1:]
    while index < len(raw_args):
        if raw_args[index] == "--config" and index + 1 < len(raw_args): explicit, index = raw_args[index + 1], index + 2
        else: argv.append(raw_args[index]); index += 1
    cfg_path = _config_path(explicit)
    if len(argv) >= 2 and argv[0].lower() == "hook":
        operation = argv[1].lower().replace("_", "-")
        if operation == "stop":
            roots = _stop_roots(cfg_path)
            if not roots: return _quiet()
            raw = sys.stdin.buffer.read(MAX_STDIN)
            event = _stop_event(raw, roots)
            return _quiet() if event is None else _bootstrap(argv, cfg_path, event, raw)
        if operation == "session-start":
            try: _pending()
            except TimeoutError: raise
            except OSError: pass
            return _quiet()
        if operation in {"pre-invocation", "user-prompt-submit", "pre-tool-use"}: return _quiet()
    return _bootstrap(argv, cfg_path)
if __name__ == "__main__":
    raise SystemExit(main())
