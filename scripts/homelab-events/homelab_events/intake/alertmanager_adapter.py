"""Pure Alertmanager webhook normalization for shadow ingestion.

The adapter returns canonical events but performs no network I/O and does not
publish notifications. Alertmanager remains responsible for evaluation,
grouping, inhibition, and silencing.
"""

from __future__ import annotations

import re

from typing import Any, Mapping

from homelab_events.store.ledger import redact_event, validate_event
from homelab_events.common import canonical_json, sha256_hex, utc_now_iso


# These names are the conservative policy's hard page allowlist mapped to the
# names used by the current Prometheus rules.
CRITICAL_EVENT_NAMES = {
    "DriveSmartFailed": "smart_overall_failure",
    "DriveReallocatedSectors": "reallocated_sectors_emergency",
    "DriveOfflineUncorrectable": "offline_uncorrectable_sectors",
    "NvmeCriticalWarning": "nvme_critical_warning",
    "BatteryCritical": "battery_shutdown_risk",
    "CitadelUnreachable": "citadel_unreachable",
}


def _snake_case(value: str) -> str:
    value = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", value)
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return value.strip("_").lower() or "alert"


def _stable_fingerprint(alert: Mapping[str, Any]) -> str:
    fingerprint = str(alert.get("fingerprint", "")).strip()
    if fingerprint:
        return fingerprint
    material = {
        "labels": alert.get("labels", {}),
        "startsAt": alert.get("startsAt", ""),
    }
    return sha256_hex(canonical_json(material))[:32]


def _observed_at(alert: Mapping[str, Any], status: str) -> str:
    if status == "resolved":
        ends_at = str(alert.get("endsAt", ""))
        if ends_at and not ends_at.startswith("0001-01-01"):
            return ends_at
    starts_at = str(alert.get("startsAt", ""))
    if starts_at:
        return starts_at
    return utc_now_iso()


def _severity(labels: Mapping[str, Any]) -> str:
    value = str(labels.get("severity", "warning")).lower()
    if value in {"critical", "warning", "info"}:
        return value
    return "warning"


def _first(labels: Mapping[str, Any], *names: str, default: str) -> str:
    for name in names:
        value = labels.get(name)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _one_event(
    alert: Mapping[str, Any],
    *,
    payload_status: str,
    payload_version: Any,
    group_labels: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(alert, Mapping):
        raise ValueError("Alertmanager alerts must be objects")
    labels: dict[str, Any] = {str(k): v for k, v in group_labels.items()}
    alert_labels = alert.get("labels", {})
    if not isinstance(alert_labels, Mapping):
        raise ValueError("Alertmanager alert labels must be an object")
    labels.update({str(k): v for k, v in alert_labels.items()})

    alertname = str(labels.get("alertname", "Alert"))
    status = str(alert.get("status", payload_status or "firing")).lower()
    if status not in {"firing", "resolved"}:
        raise ValueError(f"unsupported Alertmanager status: {status}")
    fingerprint = _stable_fingerprint(alert)
    starts_at = str(alert.get("startsAt", ""))
    event_name = CRITICAL_EVENT_NAMES.get(alertname, _snake_case(alertname))
    severity = _severity(labels)
    page_policy = (
        "critical"
        if status == "firing" and alertname in CRITICAL_EVENT_NAMES
        else "digest"
    )
    annotations = alert.get("annotations", {})
    if not isinstance(annotations, Mapping):
        raise ValueError("Alertmanager alert annotations must be an object")
    summary = str(annotations.get("summary", "")).strip()
    description = str(annotations.get("description", "")).strip()
    message = "\n".join(part for part in (summary, description) if part) or alertname
    host = _first(labels, "host", "hostname", "node", "instance", default="unknown")
    service = _first(labels, "service", "job", default=alertname)
    resource = _first(
        labels,
        "resource",
        "device",
        "container",
        "instance",
        "host",
        default=service,
    )
    event_id_material = f"{fingerprint}\0{status}\0{starts_at}"
    event_id = "am-" + sha256_hex(event_id_material)[:32]
    evidence = []
    generator_url = str(alert.get("generatorURL", "")).strip()
    if generator_url:
        evidence.append(generator_url)

    event: dict[str, Any] = {
        "schema_version": 1,
        "event_id": event_id,
        "dedupe_key": f"alertmanager/{fingerprint}",
        "source": "prometheus",
        "host": host,
        "service": service,
        "resource": resource,
        "event": event_name,
        "status": status,
        "severity": severity,
        "page_policy": page_policy,
        "observed_at": _observed_at(alert, status),
        "message": message,
        "labels": labels,
        "annotations": dict(annotations),
        "runbook": annotations.get("runbook") or annotations.get("runbook_url"),
        "evidence": evidence,
        "source_payload": {
            "alertmanager": {
                "version": payload_version,
                "groupLabels": dict(group_labels),
                "alert": dict(alert),
            }
        },
    }
    event = redact_event(event)
    validate_event(event)
    return event


def adapt_alertmanager_payload(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Convert a v4 Alertmanager webhook payload to canonical events."""

    if not isinstance(payload, Mapping):
        raise ValueError("Alertmanager payload must be an object")
    alerts = payload.get("alerts", [])
    if not isinstance(alerts, list):
        raise ValueError("Alertmanager payload alerts must be a list")
    payload_status = str(payload.get("status", "firing")).lower()
    if payload_status not in {"firing", "resolved"}:
        raise ValueError(f"unsupported Alertmanager payload status: {payload_status}")
    group_labels = payload.get("groupLabels", {})
    if not isinstance(group_labels, Mapping):
        raise ValueError("Alertmanager groupLabels must be an object")
    return [
        _one_event(
            alert,
            payload_status=payload_status,
            payload_version=payload.get("version", "unknown"),
            group_labels=group_labels,
        )
        for alert in alerts
    ]
