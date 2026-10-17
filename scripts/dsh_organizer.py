#!/usr/bin/env python3
"""DSH-native summary worker for mindie-knowledge block indexing.

The Host plugin exposes a loopback-only summary endpoint backed by the DSH
model route of the active agent. This worker implements the shared
``mindie-summary-outcome/1`` contract exactly: one identity request, one model
request, one outcome envelope. It never retries and never substitutes a
fallback index.
"""

from __future__ import annotations

import hashlib
import json
import os
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
MAX_RESPONSE_BYTES = 256 * 1024
DEADLINE_SECONDS = 300


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
    token = value.get("token")
    model = value.get("model")
    provider = value.get("provider")
    if (
        not isinstance(url, str)
        or not url.startswith("http://127.0.0.1:")
        or not isinstance(token, str)
        or not token
        or not isinstance(model, str)
        or not model
        or not isinstance(provider, str)
        or not provider
    ):
        raise ConfigurationError("the DSH summary endpoint has no active model route")
    return value


def identity() -> dict:
    from mindie_knowledge.materials import summarizer

    try:
        value = endpoint()
        model = value["model"]
        provider = value["provider"]
        implementation = {
            "worker": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "summarizer": hashlib.sha256(Path(summarizer.__file__).read_bytes()).hexdigest(),
            "provider": provider,
        }
    except ConfigurationError:
        model = "dsh/unavailable"
        implementation = {
            "worker": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "provider": "unavailable",
        }
    return summarizer.policy_identity(model=model, effort="off", implementation=implementation)


def _post(value: dict) -> tuple[str, dict]:
    body = json.dumps(value, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        value["url"],
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + value["token"],
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=DEADLINE_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(2000).decode("utf-8", "replace")
        except OSError:
            pass
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
    if not isinstance(usage, dict):
        usage = {}
    return payload["text"], usage


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("model returned no JSON object")
    value = json.loads(stripped[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("model returned a non-object")
    return value


def _usage(raw: dict) -> dict:
    def number(key):
        value = raw.get(key)
        return value if type(value) is int and value >= 0 else 0

    return {
        "input_tokens": number("input_tokens"),
        "cached_input_tokens": number("cached_input_tokens"),
        "output_tokens": number("output_tokens"),
    }


def main(argv: list[str] | None = None) -> int:
    from mindie_knowledge.materials import summarizer

    argv = list(sys.argv[1:] if argv is None else argv)
    if "--identity" in argv:
        print(json.dumps(identity(), ensure_ascii=False))
        return 0
    started = time.monotonic()
    request = None
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise ValueError("summary request exceeds the limit")
        request = json.loads(raw)
        request = summarizer.validate_request(request)
        point = endpoint()
        payload = dict(point)
        payload["request"] = request
        payload["prompt"] = summarizer.PROMPT
        text, usage = _post(payload)
        try:
            parsed = _extract_json(text)
            result = summarizer.validate_result(parsed, [b["block_id"] for b in request["blocks"]])
        except Exception as exc:  # noqa: BLE001
            envelope = summarizer.outcome(
                request,
                status="failed",
                raw_result=text if len(text.encode("utf-8")) <= summarizer.MAX_RESPONSE_BYTES else None,
                usage=_usage(usage),
                usage_known=True,
                model_calls=1,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error="invalid_result",
                error_reason="result_contract_invalid",
                billing_status="reported",
            )
            print(json.dumps(envelope, ensure_ascii=False))
            return 0
        envelope = summarizer.outcome(
            request,
            status="returned",
            result=result,
            raw_result=text if len(text.encode("utf-8")) <= summarizer.MAX_RESPONSE_BYTES else None,
            usage=_usage(usage),
            usage_known=True,
            model_calls=1,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            billing_status="reported",
        )
        print(json.dumps(envelope, ensure_ascii=False))
        return 0
    except ConfigurationError as exc:
        if request is None:
            request = {"task_id": "", "body_version": "", "batch_id": "", "input_digest": "",
                       "policy_identity": identity(), "blocks": []}
        try:
            envelope = summarizer.outcome(
                request,
                status="failed",
                usage=None,
                usage_known=False,
                model_calls=0,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error="configuration",
                error_reason="configuration_invalid",
                billing_status="not_called",
            )
            print(json.dumps(envelope, ensure_ascii=False))
            return 0
        except Exception:
            pass
        print(json.dumps({"schema": "mindie-summary-error/1", "error": str(exc)}))
        return 1
    except UncertainError as exc:
        if request is not None:
            envelope = summarizer.outcome(
                request,
                status="outcome_unknown",
                usage=None,
                usage_known=False,
                model_calls=1,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                error="unknown",
                error_reason="unknown_failure",
                billing_status="unknown",
            )
            print(json.dumps(envelope, ensure_ascii=False))
            return 0
        print(json.dumps({"schema": "mindie-summary-error/1", "error": str(exc)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
