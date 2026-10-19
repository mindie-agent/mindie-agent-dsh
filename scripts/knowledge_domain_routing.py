"""[Role] Resolve an explicit knowledge domain or ``mindie://`` reference to a trusted engine configuration.

[Invariants]
- Only the native install configuration's trusted ``domain_configs`` mapping may name another engine; public callers pass a canonical domain or a ``mindie://DOMAIN/...`` reference, never a path or map.
- A call without an explicit domain/reference keeps the installed default path and never imports the optional core selector, preserving installed-old-runtime behavior.
- Explicit routing unavailable in an old runtime fails; a cross-domain request never silently falls back to the default engine.
- Canonical domains, absolute existing configs, declared-domain agreement, reference authority and domain/reference conflicts are validated by the shared core selector, never reimplemented here.
- A target engine's own ``admission_path`` is read only to find the current session's existing active lease; no lease, token or authority is copied, created or shared.

[Timeouts & Locks]
- One bounded local JSON read for the default config in the old-runtime same-domain fallback and for the target admission path; no locks, waits, timers, retries or network.

[Side-Effect Policy]
- Read-only routing: no configuration creation, writes, service start, lease, registry, RPC or authority grant.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

_REFERENCE_AUTHORITY = re.compile(r"mindie://(?P<domain>[a-z][a-z0-9-]{0,63})/")
_ROUTING_UNAVAILABLE = (
    "explicit domain routing is unavailable in the installed knowledge runtime; "
    "update the paired runtime"
)


class DomainRoutingError(RuntimeError):
    """An explicit domain/reference cannot be routed by the installed runtime."""


@dataclass(frozen=True)
class Route:
    selected_path: Path
    default_path: Path
    forward_domain: str | None
    cross_domain: bool
    explicit: bool


def reference_domain(ref: Any) -> str | None:
    match = _REFERENCE_AUTHORITY.match(ref) if isinstance(ref, str) else None
    return match.group("domain") if match is not None else None


def default_route(default_config_path: str | Path) -> Route:
    default = Path(default_config_path).resolve()
    return Route(default, default, None, False, False)


def adapter_domain_configs(config: Mapping[str, Any]) -> Mapping[str, Any] | None:
    value = config.get("domain_configs")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DomainRoutingError("runtime domain_configs must be an object")
    return value


def _selector():
    try:
        from mindie_knowledge.loop.domain_routes import select_engine_config
    except ModuleNotFoundError as exc:
        if exc.name != "mindie_knowledge.loop.domain_routes":
            raise
        return None
    return select_engine_config


def _declared_domain(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainRoutingError(f"cannot read the default engine configuration: {path}") from exc
    domain = data.get("domain") if isinstance(data, dict) else None
    if not isinstance(domain, str) or not domain:
        raise DomainRoutingError(f"default engine configuration has no domain: {path}")
    return domain


def resolve_route(
    default_config_path: str | Path,
    *,
    domain: Any = None,
    ref: Any = None,
    domain_configs: Mapping[str, Any] | None = None,
) -> Route:
    """Resolve one route using only the shared core selector for explicit calls."""
    default = Path(default_config_path).resolve()
    if domain is None and ref is None:
        return default_route(default)

    selector = _selector()
    if selector is None:
        if domain is not None:
            raise DomainRoutingError(_ROUTING_UNAVAILABLE)
        selected_domain = reference_domain(ref)
        if selected_domain is not None and selected_domain != _declared_domain(default):
            raise DomainRoutingError(_ROUTING_UNAVAILABLE)
        # A same-domain or non-canonical reference keeps the installed default
        # path; the core Service still validates the reference itself.
        return default_route(default)

    try:
        selected = Path(
            selector(str(default), domain=domain, ref=ref, domain_configs=domain_configs)
        ).resolve()
    except (TypeError, ValueError) as exc:
        raise DomainRoutingError(str(exc)) from exc
    forward = domain if domain is not None else reference_domain(ref)
    return Route(selected, default, forward, selected != default, True)


def target_lease(
    engine_config_path: str | Path,
    session: str,
    *,
    domain: str | None,
) -> dict[str, Any]:
    """Return the current session's existing lease in the target engine's own admission."""
    path = Path(engine_config_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DomainRoutingError(f"cannot read the selected engine configuration: {path}") from exc
    admission_path = data.get("admission_path") if isinstance(data, dict) else None
    if not isinstance(admission_path, str) or not admission_path:
        raise DomainRoutingError(
            f"selected engine configuration for {domain!r} has no admission_path; "
            "feedback and distillation require that domain's existing lease"
        )
    try:
        from mindie_knowledge.loop.activation import Admission

        lease = Admission(admission_path).active_lease(session)
    except Exception as exc:  # noqa: BLE001 - report an unusable target lease store
        raise DomainRoutingError(
            f"cannot read the selected domain lease: {type(exc).__name__}: {exc}"
        ) from exc
    if not isinstance(lease, dict) or not isinstance(lease.get("token"), str) or not lease["token"]:
        raise DomainRoutingError(
            f"domain {domain!r} has no active admission lease for this session; "
            "activate that domain independently"
        )
    return lease
