#!/usr/bin/env python3
"""[Role] DSH-native summary worker implementing the mindie-summary-outcome/1 contract.
[Invariants] Calls the loopback summary endpoint once per batch; never retries uncertain model calls or fabricates fallback indexes. An explicit mindie-distill-request/1 worker request uses the shared C6 noop/prompt/schema/validation; a normal Stop is never inferred as completed.
[Timeouts & Locks] HTTP POST uses the parent's remaining budget, capped at 300s with cleanup reserve; summary request/response bytes are capped at 256 KiB and a prepared C6 request at 512 KiB.
[Side-Effect Policy] Stateless HTTP client to local DSH summary endpoint; emits a single JSON outcome envelope to stdout. Never scans ledgers or the filesystem for distillation.
"""

from __future__ import annotations

import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import mindie_paths as mp  # noqa: E402

MAX_REQUEST_BYTES = 256 * 1024
MAX_DISTILL_REQUEST_BYTES = 512 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
DEADLINE_SECONDS = 300
DISTILL_REQUEST_SCHEMA = "mindie-distill-request/1"


class ConfigurationError(RuntimeError):
    pass


class UncertainError(RuntimeError):
    """The request may have reached the model; the outcome is unknown."""


def endpoint_path() -> Path:
    return mp.state_dir() / "summary-endpoint.json"


def endpoint() -> dict:
    path = endpoint_path()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError("the DSH summary endpoint is not running") from exc
    except (OSError, ValueError) as exc:
        raise ConfigurationError("the DSH summary endpoint file is unreadable") from exc
    if not isinstance(value, dict):
        raise ConfigurationError("the DSH summary endpoint file is invalid")
    url = value.get("url")
    if (
        not isinstance(url, str)
        or not url.startswith("http://127.0.0.1:")
        or not all(isinstance(value.get(k), str) and value.get(k) for k in ("token", "model", "provider"))
    ):
        raise ConfigurationError("the DSH summary endpoint has no active model route")
    return value


DISTILL_API = (
    "value_gate", "noop_decision", "with_worker_identity", "validate_request",
    "build_prompt", "decision_schema", "validate_decision",
)


def distill_capability() -> bool:
    """Advertise C6 only when the installed runtime can actually service it."""
    try:
        from mindie_knowledge.graded import distill
    except Exception:  # pragma: no cover - the legacy runtime has no C6 module
        return False
    if getattr(distill, "REQUEST_SCHEMA", None) != DISTILL_REQUEST_SCHEMA:
        return False
    return all(callable(getattr(distill, name, None)) for name in DISTILL_API)


def identity() -> dict:
    from mindie_knowledge.materials import summarizer

    worker_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        value = endpoint()
        model = value["model"]
        implementation = {
            "worker": worker_hash,
            "summarizer": hashlib.sha256(Path(summarizer.__file__).read_bytes()).hexdigest(),
            "provider": value["provider"],
        }
    except ConfigurationError:
        model = "dsh/unavailable"
        implementation = {"worker": worker_hash, "provider": "unavailable"}
    value = summarizer.policy_identity(model=model, effort="off", implementation=implementation)
    capabilities = list(value.get("capabilities", []))
    if DISTILL_REQUEST_SCHEMA not in capabilities and distill_capability():
        capabilities.append(DISTILL_REQUEST_SCHEMA)
        value.pop("fingerprint", None)
        value["capabilities"] = capabilities
        value["fingerprint"] = summarizer.digest(value)
    return value


def _post(value: dict) -> tuple[str, dict]:
    from mindie_knowledge.loop.summary_budget import native_timeout

    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        value["url"],
        data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + value["token"]},
        method="POST",
    )
    timeout = native_timeout(DEADLINE_SECONDS)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read(2000).decode("utf-8", "replace")
        except OSError:
            detail = ""
        if exc.code == 409:
            raise ConfigurationError("the DSH session has no active model route") from exc
        raise UncertainError(f"summary endpoint HTTP {exc.code}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise UncertainError(f"summary endpoint unavailable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise UncertainError("summary endpoint exceeded the deadline") from exc
    if len(raw) > MAX_RESPONSE_BYTES:
        raise UncertainError("summary endpoint response exceeds the limit")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise UncertainError("summary endpoint returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
        raise UncertainError("summary endpoint returned no text")
    usage = payload.get("usage")
    return payload["text"], usage if isinstance(usage, dict) else {}


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model returned no JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model returned a non-object")
    if "blocks" not in value and isinstance(value.get("block_indexes"), list):
        value["blocks"] = value.pop("block_indexes")
    if isinstance(navigation := value.get("navigation"), str):
        nav_text = navigation.strip()
        first = nav_text.splitlines()[0].strip() if nav_text else "Task navigation"
        value["navigation"] = {"title": first[:120] or "Task navigation", "summary": nav_text[:1000]}
    return value


def _usage(raw: dict) -> dict | None:
    keys = ("input_tokens", "cached_input_tokens", "output_tokens")
    if any(type(raw.get(key)) is not int or raw[key] < 0 for key in keys):
        return None
    return {key: raw[key] for key in keys}


def _distill_outcome(request: dict, started: float, summarizer) -> dict:
    """Return one native outcome envelope for a validated C6 worker request."""
    from mindie_knowledge.graded import distill

    def elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    try:
        reason = distill.value_gate(request)
    except (KeyError, TypeError, ValueError):
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="configuration",
            error_reason="request_invalid", billing_status="not_called",
        )
    if reason:
        # Stop is in-flight, not completion. This is deliberately before
        # endpoint loading and prompt construction: no paid model call.
        return summarizer.outcome(
            request, status="returned", result=distill.noop_decision(request, reason),
            usage=None, usage_known=False, model_calls=0, elapsed_ms=elapsed_ms(),
            billing_status="not_called",
        )
    try:
        route = endpoint()
    except ConfigurationError:
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="configuration",
            error_reason="configuration_invalid", billing_status="not_called",
        )
    if request["policy_identity"] != identity():
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="configuration",
            error_reason="worker_policy_changed", billing_status="not_called",
        )
    try:
        prompt = distill.build_prompt(request)
        schema = distill.decision_schema()
    except (AttributeError, TypeError, ValueError):
        # An installed runtime without the shared C6 prompt/schema is not
        # silently replaced by a plugin-local prompt.
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="configuration",
            error_reason="dependency_unavailable", billing_status="not_called",
        )
    payload = {
        **route,
        "schema": request["schema_version"],
        "request": request,
        "prompt": prompt,
        "output_schema": schema,
    }
    try:
        text, raw_usage = _post(payload)
    except TimeoutError:
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="deadline",
            error_reason="native_deadline", billing_status="not_called",
        )
    except ConfigurationError:
        return summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=elapsed_ms(), error="configuration",
            error_reason="configuration_invalid", billing_status="not_called",
        )
    except UncertainError:
        return summarizer.outcome(
            request, status="outcome_unknown", usage=None, usage_known=False,
            model_calls=1, elapsed_ms=elapsed_ms(), error="unknown",
            error_reason="unknown_failure", billing_status="unknown",
        )
    reported_usage = _usage(raw_usage)
    usage_known = reported_usage is not None
    raw_result = text if len(text.encode("utf-8")) <= summarizer.MAX_RESPONSE_BYTES else None
    try:
        decision = distill.validate_decision(_extract_json(text), request)
    except Exception:  # noqa: BLE001 - untrusted model output
        # The remote call completed and was billed; local bookkeeping failure
        # must not reverse that fact into an unknown or zero-call outcome.
        return summarizer.outcome(
            request, status="failed", raw_result=raw_result, usage=reported_usage,
            usage_known=usage_known, model_calls=1, elapsed_ms=elapsed_ms(),
            error="invalid_result", error_reason="result_contract_invalid",
        )
    return summarizer.outcome(
        request, status="returned", result=decision, raw_result=raw_result,
        usage=reported_usage, usage_known=usage_known, model_calls=1,
        elapsed_ms=elapsed_ms(),
    )


def main(argv: list[str] | None = None) -> int:
    from mindie_knowledge.materials import summarizer

    argv = list(sys.argv[1:] if argv is None else argv)
    if "--identity" in argv:
        print(json.dumps(identity(), ensure_ascii=True))
        return 0
    started = time.monotonic()
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_DISTILL_REQUEST_BYTES + 1)
        if len(raw) > MAX_DISTILL_REQUEST_BYTES:
            raise ValueError("worker request exceeds the limit")
        value = json.loads(raw)
        if isinstance(value, dict) and "schema_version" in value:
            if value.get("schema_version") != DISTILL_REQUEST_SCHEMA:
                print(json.dumps({
                    "schema": "mindie-summary-error/1",
                    "error": "unsupported_request_schema",
                }, ensure_ascii=True))
                return 1
            try:
                request = summarizer.validate_request(value)
            except (KeyError, TypeError, ValueError):
                print(json.dumps({
                    "schema": "mindie-summary-error/1",
                    "error": "invalid_distill_request",
                }, ensure_ascii=True))
                return 1
            envelope = _distill_outcome(request, started, summarizer)
            print(json.dumps(envelope, ensure_ascii=True))
            return 0
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("summary request exceeds the limit")
        request = summarizer.validate_request(value)
        payload = {**endpoint(), "request": request, "prompt": summarizer.PROMPT}
        text, usage = _post(payload)
        reported_usage = _usage(usage)
        usage_known = reported_usage is not None
        raw_result = text if len(text.encode("utf-8")) <= summarizer.MAX_RESPONSE_BYTES else None
        elapsed_ms = int((time.monotonic() - started) * 1000)
        try:
            result = summarizer.validate_result(
                _extract_json(text), [b["block_id"] for b in request["blocks"]]
            )
        except Exception:  # noqa: BLE001
            envelope = summarizer.outcome(
                request, status="failed", raw_result=raw_result, usage=reported_usage,
                usage_known=usage_known, model_calls=1, elapsed_ms=elapsed_ms,
                error="invalid_result", error_reason="result_contract_invalid",
            )
            print(json.dumps(envelope, ensure_ascii=True))
            return 0
        envelope = summarizer.outcome(
            request, status="returned", result=result, raw_result=raw_result,
            usage=reported_usage, usage_known=usage_known, model_calls=1,
            elapsed_ms=elapsed_ms,
        )
        print(json.dumps(envelope, ensure_ascii=True))
        return 0
    except TimeoutError:
        envelope = summarizer.outcome(
            request, status="failed", usage=None, usage_known=False, model_calls=0,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            error="deadline", error_reason="native_deadline", billing_status="not_called",
        )
        print(json.dumps(envelope, ensure_ascii=True))
        return 0
    except ConfigurationError as exc:
        if request is None:
            request = {
                "task_id": "", "body_version": "", "batch_id": "", "input_digest": "",
                "policy_identity": identity(), "blocks": [],
            }
        try:
            envelope = summarizer.outcome(
                request, status="failed", usage=None, usage_known=False, model_calls=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error="configuration", error_reason="configuration_invalid",
                billing_status="not_called",
            )
            print(json.dumps(envelope, ensure_ascii=True))
            return 0
        except Exception:
            pass
        print(json.dumps({"schema": "mindie-summary-error/1", "error": str(exc)}, ensure_ascii=True))
        return 1
    except UncertainError as exc:
        if request is not None:
            envelope = summarizer.outcome(
                request, status="outcome_unknown", usage=None, usage_known=False,
                model_calls=1, elapsed_ms=int((time.monotonic() - started) * 1000),
                error="unknown", error_reason="unknown_failure", billing_status="unknown",
            )
            print(json.dumps(envelope, ensure_ascii=True))
            return 0
        print(json.dumps({"schema": "mindie-summary-error/1", "error": str(exc)}, ensure_ascii=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
