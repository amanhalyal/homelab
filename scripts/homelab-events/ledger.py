"""Durable event and incident ledger operations.

This prototype deliberately owns persistence and notification intent only. It
is not a replacement for Prometheus rule evaluation or Alertmanager grouping.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from schema import connect

VALID_SEVERITIES = {"info", "warning", "critical"}
VALID_PAGE_POLICIES = {"log", "digest", "critical"}
VALID_STATUSES = {
    "firing",
    "resolved",
    "success",
    "failure",
    "action_started",
    "action_finished",
}
ACTIVE_STATUSES = {"firing", "failure"}
RESOLVED_STATUSES = {"resolved", "success"}
SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "api_key",
    "apikey",
    "private_key",
    "credential",
    "cookie",
)
SENSITIVE_TEXT_PATTERNS = (
    (re.compile(r"(?i)(basic\s+)[A-Za-z0-9+/=._:-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(\b(?:token|password|passwd|secret|api[_-]?key)\s*[=:]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
)


def _redact(value: Any, key: str | None = None) -> Any:
    """Redact credential-shaped fields before any payload is persisted."""

    if key and any(part in key.lower() for part in SENSITIVE_KEY_PARTS):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in SENSITIVE_TEXT_PATTERNS:
            value = pattern.sub(replacement, value)
    return value


def redact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    """Return an event copy safe to pass across a persistence boundary."""

    return _redact(dict(event))


class ControlPlaneError(sqlite3.OperationalError):
    """A structured, observable failure while writing the local control plane."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = f"{code}: {detail}" if detail else code
        super().__init__(message)


@dataclass(frozen=True)
class IngestResult:
    event_id: str
    incident_id: int
    duplicate: bool
    state: str
    page_intent_created: bool
    delivery_id: str | None


def utc_now() -> float:
    return time.time()


def _validate_timestamp(value: Any) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError("observed_at must be a non-empty ISO-8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("observed_at must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must include a timezone")


def validate_event(event: Mapping[str, Any]) -> None:
    """Validate the canonical v1 envelope before opening a write transaction."""

    if not isinstance(event, Mapping):
        raise ValueError("event must be an object")
    required = (
        "schema_version",
        "event_id",
        "dedupe_key",
        "source",
        "host",
        "service",
        "resource",
        "event",
        "status",
        "severity",
        "page_policy",
        "observed_at",
        "message",
    )
    missing = [key for key in required if not event.get(key)]
    if missing:
        raise ValueError("missing required event fields: " + ", ".join(missing))
    if event["schema_version"] != 1:
        raise ValueError("unsupported schema_version")
    if event["status"] not in VALID_STATUSES:
        raise ValueError("unsupported status")
    if event["severity"] not in VALID_SEVERITIES:
        raise ValueError("unsupported severity")
    if event["page_policy"] not in VALID_PAGE_POLICIES:
        raise ValueError("unsupported page_policy")
    _validate_timestamp(event["observed_at"])
    for key in ("labels", "annotations"):
        if key in event and not isinstance(event[key], Mapping):
            raise ValueError(f"{key} must be an object")
    if "evidence" in event and not isinstance(event["evidence"], list):
        raise ValueError("evidence must be a list")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _delivery_id(dedupe_key: str, cycle: int, kind: str) -> str:
    material = f"{kind}\0{dedupe_key}\0{cycle}".encode("utf-8")
    return f"dlv-{hashlib.sha256(material).hexdigest()[:32]}"


class Ledger:
    """Single-writer SQLite ledger facade."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        critical_topic: str = "homelab-critical",
        timeout: float = 5.0,
        read_only: bool = False,
    ) -> None:
        self.connection = connect(
            db_path,
            timeout=timeout,
            read_only=read_only,
        )
        self.critical_topic = critical_topic
        self._event_store_write_failures = 0
        self._intake_timeouts = 0
        self._digest_failures = 0

    def close(self) -> None:
        self.connection.close()

    def claim_remote_request(
        self,
        body: bytes,
        timestamp: int | str,
        signature: str,
        *,
        now: float | None = None,
        max_skew: float = 300.0,
        prune_limit: int = 100,
    ) -> bool:
        """Atomically claim a non-secret remote request identity.

        The caller must verify the HMAC and freshness before calling this
        method. Only digests are persisted; the body, signature, and secret
        never cross the SQLite boundary. A repeated exact request is rejected
        until its bounded replay record expires. Event-ID idempotency remains
        the separate protection for retries with a new timestamp/signature.
        """

        if not isinstance(body, bytes):
            raise ValueError("remote request body must be bytes")
        if not isinstance(signature, str) or not signature:
            raise ValueError("remote request signature must be non-empty")
        try:
            request_timestamp = int(timestamp)
        except (TypeError, ValueError) as exc:
            raise ValueError("remote request timestamp must be an integer") from exc
        if max_skew < 0:
            raise ValueError("remote request freshness window must be non-negative")
        if prune_limit < 1:
            raise ValueError("remote replay prune limit must be positive")

        current = utc_now() if now is None else float(now)
        body_digest = hashlib.sha256(body).hexdigest()
        signature_digest = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        request_id = hashlib.sha256(
            f"{request_timestamp}\0{body_digest}\0{signature_digest}".encode("ascii")
        ).hexdigest()
        expires_at = current + float(max_skew)

        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                DELETE FROM remote_request_replays
                WHERE request_id IN (
                    SELECT request_id FROM remote_request_replays
                    WHERE expires_at <= ?
                    ORDER BY expires_at ASC, request_id ASC
                    LIMIT ?
                )
                """,
                (current, prune_limit),
            )
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO remote_request_replays(
                    request_id, body_digest, signature_digest,
                    request_timestamp, first_seen, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    request_id,
                    body_digest,
                    signature_digest,
                    request_timestamp,
                    current,
                    expires_at,
                ),
            ).rowcount
            connection.execute("COMMIT")
            return inserted == 1
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                self._intake_timeouts += 1
                raise ControlPlaneError(
                    "intake_timeout",
                    "SQLite remote replay claim exceeded the configured timeout",
                ) from exc
            raise ControlPlaneError(
                "remote_replay_store_failed",
                "SQLite rejected the remote replay claim",
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def ingest(self, event: Mapping[str, Any]) -> IngestResult:
        """Persist an event and incident transition atomically.

        A duplicate event_id is an idempotent no-op. A critical active event
        creates one deterministic outbox intent per incident cycle; this method
        does not deliver it.
        """

        validate_event(event)
        event_id = str(event["event_id"])
        dedupe_key = str(event["dedupe_key"])
        observed_at = str(event["observed_at"])
        active = event["status"] in ACTIVE_STATUSES
        resolved = event["status"] in RESOLVED_STATUSES
        now = utc_now()
        safe_event: dict[str, Any] = _redact(dict(event))
        raw_payload = _canonical_json(safe_event)
        envelope = _canonical_json(
            {
                key: safe_event.get(key)
                for key in (
                    "schema_version",
                    "event_id",
                    "dedupe_key",
                    "source",
                    "host",
                    "service",
                    "resource",
                    "event",
                    "status",
                    "severity",
                    "page_policy",
                    "observed_at",
                    "message",
                    "labels",
                    "annotations",
                    "runbook",
                    "evidence",
                )
                if key in safe_event
            }
        )

        connection = self.connection
        try:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO events(
                    event_id, dedupe_key, source, host, service, resource, event,
                    status, severity, page_policy, observed_at, message,
                    raw_payload_json, envelope_json, inserted_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    dedupe_key,
                    str(safe_event["source"]),
                    str(safe_event["host"]),
                    str(safe_event["service"]),
                    str(safe_event["resource"]),
                    str(safe_event["event"]),
                    str(safe_event["status"]),
                    str(safe_event["severity"]),
                    str(safe_event["page_policy"]),
                    observed_at,
                    str(safe_event["message"]),
                    raw_payload,
                    envelope,
                    now,
                ),
            ).rowcount
            if not inserted:
                connection.execute("COMMIT")
                row = connection.execute(
                    "SELECT id, state FROM incidents WHERE dedupe_key = ?",
                    (dedupe_key,),
                ).fetchone()
                if row is None:
                    raise RuntimeError("duplicate event has no incident")
                return IngestResult(
                    event_id=event_id,
                    incident_id=int(row["id"]),
                    duplicate=True,
                    state=str(row["state"]),
                    page_intent_created=False,
                    delivery_id=None,
                )

            incident = connection.execute(
                "SELECT * FROM incidents WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            transition_state: str
            if incident is None:
                state = "open" if active else ("resolved" if resolved else "observed")
                insert = connection.execute(
                    """
                    INSERT INTO incidents(
                        dedupe_key, cycle, state, severity, page_policy,
                        first_seen, last_seen, occurrence_count, human_paged,
                        created_at, updated_at
                    ) VALUES (?, 1, ?, ?, ?, ?, ?, 1, 0, ?, ?)
                    """,
                    (
                        dedupe_key,
                        state,
                        str(safe_event["severity"]),
                        str(safe_event["page_policy"]),
                        observed_at,
                        observed_at,
                        now,
                        now,
                    ),
                )
                if insert.lastrowid is None:
                    raise RuntimeError("incident insert did not return an id")
                incident_id = int(insert.lastrowid)
                transition_state = state
                cycle = 1
                human_paged = False
            else:
                incident_id = int(incident["id"])
                cycle = int(incident["cycle"])
                human_paged = bool(incident["human_paged"])
                previous_state = str(incident["state"])
                if active and previous_state in {"resolved", "closed", "expired", "stale"}:
                    cycle += 1
                    human_paged = False
                    transition_state = "reopened"
                    connection.execute(
                        """
                        UPDATE incidents
                        SET cycle = ?, state = 'open', severity = ?, page_policy = ?,
                            first_seen = ?, last_seen = ?, occurrence_count = 1,
                            human_paged = 0, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            cycle,
                            str(safe_event["severity"]),
                            str(safe_event["page_policy"]),
                            observed_at,
                            observed_at,
                            now,
                            incident_id,
                        ),
                    )
                else:
                    state = "open" if active else ("resolved" if resolved else previous_state)
                    transition_state = state if state != previous_state else "observed"
                    connection.execute(
                        """
                        UPDATE incidents
                        SET state = ?, severity = ?, page_policy = ?, last_seen = ?,
                            occurrence_count = occurrence_count + 1,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            state,
                            str(safe_event["severity"]),
                            str(safe_event["page_policy"]),
                            observed_at,
                            now,
                            incident_id,
                        ),
                    )

            connection.execute(
                """
                INSERT INTO transitions(incident_id, event_id, state, detail, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    event_id,
                    transition_state,
                    str(safe_event.get("message", "")),
                    now,
                ),
            )

            # Cerberus is optional enrichment only. Queue digest candidates after
            # the event/incident decision is known; critical events deliberately
            # bypass the model and its failure domain.
            if safe_event["page_policy"] == "digest" and safe_event["status"] in {
                "firing",
                "failure",
                "resolved",
            }:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO cerberus_enrichments(
                        event_id, state, attempts, next_attempt_at, created_at, updated_at
                    ) VALUES (?, 'pending', 0, 0, ?, ?)
                    """,
                    (event_id, now, now),
                )

            page_intent_created = False
            delivery_id = None
            if active and event["page_policy"] == "critical" and not human_paged:
                delivery_id = _delivery_id(dedupe_key, cycle, "critical")
                payload = {
                    "delivery_id": delivery_id,
                    "incident_id": incident_id,
                    "cycle": cycle,
                    "event_id": event_id,
                    "dedupe_key": dedupe_key,
                    "severity": safe_event["severity"],
                    "title": f"[{str(safe_event['severity']).upper()}] {safe_event['event']}",
                    "message": safe_event["message"],
                    "observed_at": observed_at,
                }
                result = connection.execute(
                    """
                    INSERT OR IGNORE INTO outbox(
                        delivery_id, incident_id, cycle, kind, topic, payload_json,
                        state, attempts, next_attempt_at, created_at
                    ) VALUES (?, ?, ?, 'critical', ?, ?, 'pending', 0, 0, ?)
                    """,
                    (
                        delivery_id,
                        incident_id,
                        cycle,
                        self.critical_topic,
                        _canonical_json(payload),
                        now,
                    ),
                )
                page_intent_created = result.rowcount == 1
                if page_intent_created:
                    connection.execute(
                        "UPDATE incidents SET human_paged = 1, updated_at = ? WHERE id = ?",
                        (now, incident_id),
                    )

            connection.execute("COMMIT")
            final_state = str(
                connection.execute(
                    "SELECT state FROM incidents WHERE id = ?", (incident_id,)
                ).fetchone()[0]
            )
            return IngestResult(
                event_id=event_id,
                incident_id=incident_id,
                duplicate=False,
                state=final_state,
                page_intent_created=page_intent_created,
                delivery_id=delivery_id if page_intent_created else None,
            )
        except sqlite3.OperationalError as exc:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            self._event_store_write_failures += 1
            message = str(exc).lower()
            if "locked" in message or "busy" in message:
                self._intake_timeouts += 1
                raise ControlPlaneError(
                    "intake_timeout",
                    "SQLite write lock exceeded the configured timeout",
                ) from exc
            raise ControlPlaneError(
                "store_write_failed",
                "SQLite rejected the event write",
            ) from exc
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise

    def mark_expired(
        self,
        event_ids: list[str],
        *,
        reason: str,
        expired_at: float | None = None,
    ) -> int:
        """Explicitly mark event payloads eligible for retention purge."""

        ids = list(dict.fromkeys(str(event_id) for event_id in event_ids))
        if not ids:
            return 0
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("retention expiry reason must be non-empty")
        timestamp = utc_now() if expired_at is None else float(expired_at)
        placeholders = ",".join("?" for _ in ids)
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = connection.execute(
                f"""
                UPDATE events
                SET retention_state = 'expired', expired_at = ?, expiry_reason = ?
                WHERE retention_state = 'active' AND event_id IN ({placeholders})
                """,
                (timestamp, str(_redact(reason)), *ids),
            )
            connection.execute("COMMIT")
            return int(result.rowcount)
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def purge_expired_payloads(
        self,
        *,
        before: float | None = None,
        limit: int = 100,
    ) -> int:
        """Purge explicitly expired raw/envelope payloads, retaining summaries."""

        if limit < 1:
            return 0
        cutoff = utc_now() if before is None else float(before)
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """
                SELECT event_id FROM events
                WHERE retention_state = 'expired' AND expired_at IS NOT NULL
                  AND expired_at <= ?
                ORDER BY expired_at ASC, event_id ASC
                LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
            for row in rows:
                event_id = str(row["event_id"])
                tombstone = _canonical_json(
                    {"event_id": event_id, "retention": "raw_payload_purged"}
                )
                connection.execute(
                    """
                    UPDATE events
                    SET raw_payload_json = ?, envelope_json = ?, retention_state = 'purged'
                    WHERE event_id = ? AND retention_state = 'expired'
                    """,
                    (tombstone, tombstone, event_id),
                )
            connection.execute("COMMIT")
            return len(rows)
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def digest_events(self, start: str, end: str) -> list[sqlite3.Row]:
        """Return an ordered, replayable event window without consuming it."""

        return list(
            self.connection.execute(
                """
                SELECT * FROM events
                WHERE observed_at >= ? AND observed_at < ?
                ORDER BY observed_at ASC, event_id ASC
                """,
                (start, end),
            ).fetchall()
        )

    def set_cursor(self, name: str, value: str) -> None:
        now = utc_now()
        self.connection.execute(
            """
            INSERT INTO cursors(name, value, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (name, value, now),
        )

    def get_cursor(self, name: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM cursors WHERE name = ?", (name,)
        ).fetchone()
        return str(row["value"]) if row else None

    def metrics(self, *, now: float | None = None) -> dict[str, int | float]:
        """Return a read-only control-plane health snapshot."""

        current = utc_now() if now is None else float(now)

        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            row = self.connection.execute(sql, params).fetchone()
            return int(row[0]) if row else 0

        latest_row = self.connection.execute(
            "SELECT max(inserted_at) AS latest FROM events"
        ).fetchone()
        latest = float(latest_row["latest"]) if latest_row and latest_row["latest"] else None
        oldest_pending_row = self.connection.execute(
            """
            SELECT min(created_at) AS oldest FROM outbox
            WHERE state = 'pending'
            """
        ).fetchone()
        oldest_pending = (
            float(oldest_pending_row["oldest"])
            if oldest_pending_row and oldest_pending_row["oldest"]
            else None
        )
        digest_success = self.get_cursor("digest.last_success_timestamp")
        try:
            digest_success_value = float(digest_success) if digest_success else 0.0
        except ValueError:
            digest_success_value = 0.0

        return {
            "alert_events_ingested_total": count("SELECT count(*) FROM events"),
            "alert_incidents_open": count(
                "SELECT count(*) FROM incidents WHERE state = 'open'"
            ),
            "alert_pages_total": count(
                "SELECT count(*) FROM outbox WHERE kind = 'critical'"
            ),
            "alert_page_failures_total": count(
                "SELECT count(*) FROM outbox WHERE kind = 'critical' AND state = 'failed'"
            ),
            "alert_digest_last_success_timestamp": digest_success_value,
            "alert_event_store_write_failures_total": self._event_store_write_failures,
            "alert_intake_timeout_total": self._intake_timeouts,
            "alert_digest_failures_total": self._digest_failures,
            "alert_intake_age_seconds": max(0.0, current - latest) if latest else 0.0,
            "alert_outbox_pending": count("SELECT count(*) FROM outbox WHERE state = 'pending'"),
            "alert_outbox_leased": count("SELECT count(*) FROM outbox WHERE state = 'leased'"),
            "alert_outbox_failed": count("SELECT count(*) FROM outbox WHERE state = 'failed'"),
            "alert_outbox_oldest_pending_age_seconds": (
                max(0.0, current - oldest_pending) if oldest_pending else 0.0
            ),
        }
