#!/usr/bin/env python3
"""Emit one canonical producer event without replacing legacy notifications."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Mapping

from homelab_events.producers.producer_emit import EmissionResult, emit_local_event, emit_remote_event
from homelab_events.common import canonical_json, sha256_hex


def _object(value: str | None, name: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def _list(value: str | None, name: str) -> list[Any]:
    if not value:
        return []
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError(f"{name} must be a JSON list")
    return parsed


def build_event(
    *,
    source: str,
    host: str,
    service: str,
    resource: str,
    event_name: str,
    status: str,
    severity: str,
    page_policy: str,
    message: str,
    dedupe_key: str | None = None,
    observed_at: str | None = None,
    event_id: str | None = None,
    labels: Mapping[str, Any] | None = None,
    annotations: Mapping[str, Any] | None = None,
    runbook: str | None = None,
    evidence: list[Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical v1 event with a stable ID for identical observations."""

    observation = {
        "schema_version": 1,
        "dedupe_key": dedupe_key
        or f"{service}/{host}/{resource.strip('/') or 'root'}/{event_name}",
        "source": source,
        "host": host,
        "service": service,
        "resource": resource,
        "event": event_name,
        "status": status,
        "severity": severity,
        "page_policy": page_policy,
        "observed_at": observed_at
        or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "message": message,
        "labels": dict(labels or {}),
        "annotations": dict(annotations or {}),
        "evidence": list(evidence or []),
    }
    if runbook:
        observation["runbook"] = runbook
    if event_id:
        observation["event_id"] = event_id
    else:
        observation["event_id"] = "evt-" + sha256_hex(canonical_json(observation))[:32]
    return observation


def _result(result: EmissionResult) -> dict[str, Any]:
    return {"ok": result.ok, **asdict(result)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("legacy", "shadow", "canonical", "remote"), default="legacy")
    parser.add_argument("--event-json")
    parser.add_argument("--source")
    parser.add_argument("--host")
    parser.add_argument("--service")
    parser.add_argument("--resource")
    parser.add_argument("--event-name")
    parser.add_argument("--status")
    parser.add_argument("--severity")
    parser.add_argument("--page-policy")
    parser.add_argument("--message")
    parser.add_argument("--dedupe-key")
    parser.add_argument("--observed-at")
    parser.add_argument("--event-id")
    parser.add_argument("--labels-json")
    parser.add_argument("--annotations-json")
    parser.add_argument("--evidence-json")
    parser.add_argument("--runbook")
    parser.add_argument("--db", default="/opt/homelab/data/homelab-events/events.db")
    parser.add_argument("--spool", default="/opt/homelab/data/homelab-events/spool")
    parser.add_argument("--policy", default="/opt/homelab/scripts/homelab-events/config/policy.yaml")
    parser.add_argument("--policy-context", default="{}")
    parser.add_argument("--remote-endpoint")
    parser.add_argument("--remote-secret-file")
    parser.add_argument("--remote-spool")
    args = parser.parse_args(argv)

    try:
        if args.event_json:
            event = json.loads(args.event_json)
            if not isinstance(event, dict):
                raise ValueError("--event-json must be a JSON object")
        else:
            required = {
                "source": args.source,
                "host": args.host,
                "service": args.service,
                "resource": args.resource,
                "event-name": args.event_name,
                "status": args.status,
                "severity": args.severity,
                "page-policy": args.page_policy,
                "message": args.message,
            }
            missing = [name for name, value in required.items() if value is None]
            if missing:
                raise ValueError("missing producer fields: " + ", ".join(missing))
            event = build_event(
                source=args.source,
                host=args.host,
                service=args.service,
                resource=args.resource,
                event_name=args.event_name,
                status=args.status,
                severity=args.severity,
                page_policy=args.page_policy,
                message=args.message,
                dedupe_key=args.dedupe_key,
                observed_at=args.observed_at,
                event_id=args.event_id,
                labels=_object(args.labels_json, "--labels-json"),
                annotations=_object(args.annotations_json, "--annotations-json"),
                runbook=args.runbook,
                evidence=_list(args.evidence_json, "--evidence-json"),
            )
        if args.mode == "legacy":
            print(json.dumps({"ok": False, "enabled": False, "reason": "mode_disabled"}, sort_keys=True))
            return 0
        if args.mode == "remote":
            result = emit_remote_event(
                event,
                endpoint=args.remote_endpoint,
                secret_file=args.remote_secret_file,
                spool_dir=args.remote_spool or args.spool,
            )
        else:
            import json as _json_module

            context = _json_module.loads(args.policy_context)
            if not isinstance(context, dict):
                raise ValueError("--policy-context must be a JSON object")
            result = emit_local_event(
                event,
                mode=args.mode,
                db_path=args.db,
                spool_dir=args.spool,
                policy_path=args.policy,
                context=context,
            )
        print(json.dumps(_result(result), sort_keys=True))
        if args.mode in {"canonical", "remote"} and not (result.ok or result.spooled):
            return 75
        return 0
    except Exception:
        # This CLI is called beside legacy alerts; never turn an intake issue
        # into a producer outage, and never print exception/payload contents.
        print(json.dumps({"ok": False, "enabled": args.mode != "legacy", "reason": "invalid_or_unavailable"}, sort_keys=True))
        return 75 if args.mode in {"canonical", "remote"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
