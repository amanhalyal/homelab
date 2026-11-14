"""Best-effort, internal-only producer emission for shadow evaluation."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from canonical_intake import gate_event, ingest_event
from ledger import Ledger, redact_event, validate_event
from notify_gate import load_policy
from remote_auth import sign_payload
from spool import EventSpool


_INTERNAL_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", "homelab-event-shadow"})
# Edit these for a deployment: Legion's Tailscale address and name, and the
# TLS hostname that fronts the receiver. Anything else is refused.
_REMOTE_HOSTS = frozenset(
    {
        "127.0.0.1",
        "::1",
        "localhost",
        "100.64.0.1",
        "legion",
        "events.example.duckdns.org",
    }
)


@dataclass(frozen=True)
class EmissionResult:
    enabled: bool
    ok: bool
    status: int | None
    reason: str
    action: str | None = None
    spooled: bool = False
    duplicate: bool = False


def _allowed_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(endpoint)
    return (
        parsed.scheme == "http"
        and parsed.hostname in _INTERNAL_HOSTS
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path == "/event"
    )


def emit_shadow_event(
    event: Mapping[str, Any],
    *,
    endpoint: str | None = None,
    timeout: float = 5.0,
) -> EmissionResult:
    """Send one redacted event to the internal shadow endpoint, if configured."""

    target = endpoint if endpoint is not None else os.environ.get("HOMELAB_EVENTS_SHADOW_URL", "")
    if not target:
        return EmissionResult(False, False, None, "endpoint_disabled")
    if not _allowed_endpoint(target):
        return EmissionResult(False, False, None, "endpoint_not_allowed")
    try:
        validate_event(event)
        safe_event = redact_event(event)
        body = json.dumps(
            safe_event,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            target,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
        if 200 <= status < 300:
            return EmissionResult(True, True, status, "accepted")
        return EmissionResult(True, False, status, "endpoint_rejected")
    except (TypeError, ValueError):
        return EmissionResult(True, False, None, "invalid_event")
    except HTTPError as error:
        return EmissionResult(True, False, int(error.code), "endpoint_http_error")
    except (TimeoutError, URLError, OSError):
        return EmissionResult(True, False, None, "endpoint_unreachable")


def _allowed_remote_endpoint(endpoint: str) -> bool:
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        return False
    local = parsed.hostname in {"127.0.0.1", "::1", "localhost"}
    tailnet = parsed.hostname in {"100.64.0.1", "legion"}
    secure_proxy = (
        parsed.hostname == "events.example.duckdns.org"
        and parsed.scheme == "https"
        and parsed.port in {None, 443}
    )
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname in _REMOTE_HOSTS
        and (
            (local and port is not None)
            or (tailnet and port in {8089, 443})
            or secure_proxy
        )
        and not parsed.username
        and not parsed.password
        and not parsed.query
        and not parsed.fragment
        and parsed.path == "/remote-event"
    )


def emit_remote_event(
    event: Mapping[str, Any],
    *,
    endpoint: str | None = None,
    secret: str | None = None,
    secret_file: str | None = None,
    timestamp: int | None = None,
    timeout: float = 5.0,
    spool_dir: str | None = None,
) -> EmissionResult:
    """Send a redacted, timestamped HMAC event to Legion or spool it locally."""

    target = endpoint if endpoint is not None else os.environ.get(
        "HOMELAB_EVENTS_REMOTE_URL", ""
    )
    if not target or not _allowed_remote_endpoint(target):
        return EmissionResult(False, False, None, "endpoint_not_allowed")
    resolved_secret = secret
    if resolved_secret is None:
        if secret_file is None:
            secret_file = os.environ.get("HOMELAB_EVENTS_REMOTE_SECRET_FILE", "")
        if secret_file:
            try:
                resolved_secret = open(secret_file, encoding="utf-8").read().strip()
            except OSError:
                resolved_secret = ""
        else:
            resolved_secret = os.environ.get("HOMELAB_EVENTS_REMOTE_SECRET", "")
    safe_event: dict[str, Any] | None = None
    try:
        validate_event(event)
        safe_event = redact_event(event)
        body = json.dumps(
            safe_event, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode("utf-8")
        stamp = int(time.time()) if timestamp is None else int(timestamp)
        request = Request(
            target,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Homelab-Event-Timestamp": str(stamp),
                "X-Homelab-Event-Signature": sign_payload(
                    body, resolved_secret, timestamp=stamp
                ),
            },
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
        if 200 <= status < 300:
            return EmissionResult(True, True, status, "accepted")
        return EmissionResult(True, False, status, "endpoint_rejected")
    except (TypeError, ValueError):
        return EmissionResult(True, False, None, "invalid_event")
    except HTTPError as error:
        failure = EmissionResult(True, False, int(error.code), "endpoint_http_error")
    except (TimeoutError, URLError, OSError):
        failure = EmissionResult(True, False, None, "endpoint_unreachable")
    if spool_dir and safe_event is not None:
        try:
            EventSpool(spool_dir).append(safe_event)
            return EmissionResult(
                failure.enabled,
                failure.ok,
                failure.status,
                failure.reason,
                spooled=True,
            )
        except Exception:
            pass
    return failure


def emit_local_event(
    event: Mapping[str, Any],
    *,
    mode: str | None = None,
    db_path: str | None = None,
    spool_dir: str | None = None,
    policy_path: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> EmissionResult:
    """Persist a producer event locally when shadow/canonical mode is explicit."""

    selected_mode = mode if mode is not None else os.environ.get(
        "HOMELAB_EVENTS_MODE", "legacy"
    )
    if selected_mode not in {"shadow", "canonical"}:
        return EmissionResult(False, False, None, "mode_disabled")
    database = db_path or os.environ.get(
        "HOMELAB_EVENTS_DB", "/opt/homelab/data/homelab-events/events.db"
    )
    spool_path = spool_dir or os.environ.get(
        "HOMELAB_EVENTS_SPOOL", "/opt/homelab/data/homelab-events/spool"
    )
    policy_file = policy_path or os.environ.get(
        "HOMELAB_EVENTS_POLICY",
        "/opt/homelab/scripts/homelab-events/policy.yaml",
    )
    policy = load_policy(policy_file)
    safe_event, decision = gate_event(event, policy, context)
    try:
        ledger = Ledger(database, critical_topic=policy.critical_topic)
    except Exception:
        EventSpool(spool_path).append(safe_event)
        return EmissionResult(
            True, False, None, "ledger_unavailable_spooled", decision.action, True
        )
    try:
        result = ingest_event(event, ledger, EventSpool(spool_path), policy, context)
        return EmissionResult(
            True,
            True,
            200,
            "spooled" if result.spooled else "accepted",
            result.decision.action,
            result.spooled,
            bool(result.ingest.duplicate if result.ingest is not None else False),
        )
    finally:
        ledger.close()
