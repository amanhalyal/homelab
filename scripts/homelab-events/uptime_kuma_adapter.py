"""Normalize Uptime Kuma webhook notifications into canonical events."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from producer_cli import build_event


def _observed_at(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Uptime Kuma heartbeat time is required")
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _status(value: Any) -> tuple[str, str, str]:
    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Uptime Kuma heartbeat status must be 0 or 1") from exc
    if numeric == 0:
        return "firing", "warning", "down"
    if numeric == 1:
        return "resolved", "info", "up"
    raise ValueError("Uptime Kuma heartbeat status must be 0 or 1")


def adapt_uptime_kuma_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return one stable canonical event for a Kuma webhook payload.

    Kuma's webhook provider sends ``monitor``, ``heartbeat``, and ``msg``.
    Monitor URLs are intentionally not copied into the event because a URL can
    contain credentials; the event ledger keeps the monitor identity and state
    while the Kuma UI remains the URL-bearing source of truth.
    """

    if not isinstance(payload, Mapping):
        raise ValueError("Uptime Kuma payload must be an object")
    monitor = payload.get("monitor")
    heartbeat = payload.get("heartbeat")
    if not isinstance(monitor, Mapping) or not isinstance(heartbeat, Mapping):
        raise ValueError("Uptime Kuma payload requires monitor and heartbeat objects")

    monitor_id = monitor.get("id")
    monitor_name = str(monitor.get("name") or monitor_id or "unknown-monitor")
    monitor_key = str(monitor_id or monitor_name)
    status, severity, state = _status(heartbeat.get("status"))
    observed_at = _observed_at(heartbeat.get("time"))
    message = str(heartbeat.get("msg") or payload.get("msg") or monitor_name)
    heartbeat_id = heartbeat.get("id")
    identity = str(heartbeat_id) if heartbeat_id is not None else message
    dedupe_key = f"uptime-kuma/{monitor_key}"
    event_material = {
        "dedupe_key": dedupe_key,
        "heartbeat_identity": identity,
        "observed_at": observed_at,
        "status": status,
        "message": message,
    }
    event_id = "evt-uk-" + hashlib.sha256(
        json.dumps(event_material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    host = str(monitor.get("hostname") or "legion")
    event = build_event(
        source="uptime-kuma",
        host=host,
        service="uptime-kuma",
        resource=monitor_key,
        event_name="uptime_monitor",
        status=status,
        severity=severity,
        page_policy="digest",
        message=f"{monitor_name} {state}: {message}",
        dedupe_key=dedupe_key,
        observed_at=observed_at,
        event_id=event_id,
        labels={
            "monitor_id": monitor_key,
            "monitor_name": monitor_name,
            "monitor_type": str(monitor.get("type") or "unknown"),
            "state": state,
        },
    )
    return [event]
