"""Durable, optional Cerberus enrichment for digest-routed events.

Cerberus never decides whether a critical notification is sent. The ledger
persists the event and policy decision first; this module only leases
non-critical digest candidates and stores a bounded {what, since, check} brief.
"""

from __future__ import annotations

import json
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from homelab_events.store.ledger import Ledger
from homelab_events.common import compact_json

DEFAULT_ENDPOINT = "http://127.0.0.1:8081/v1/chat/completions"
DEFAULT_MODEL = "cerberus"
DEFAULT_TIMEOUT = 30.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_FIELD_LENGTH = 280
BRIEF_FIELDS = ("what", "since", "check")


class CerberusUnavailable(RuntimeError):
    """The optional model could not produce a bounded brief."""


@dataclass(frozen=True)
class EnrichmentItem:
    event_id: str
    attempts: int
    event: dict[str, Any]


@dataclass(frozen=True)
class DrainResult:
    claimed: int
    enriched: int
    failed: int


class CerberusClient:
    """Small HTTP client with strict output and no response-body persistence."""

    def __init__(
        self,
        *,
        endpoint: str = DEFAULT_ENDPOINT,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        if not endpoint.startswith(("http://", "https://")):
            raise ValueError("Cerberus endpoint must use HTTP(S)")
        if timeout <= 0:
            raise ValueError("Cerberus timeout must be positive")
        self.endpoint = endpoint
        self.model = model
        self.timeout = timeout
        self._opener = opener

    def brief(self, event: Mapping[str, Any]) -> dict[str, str]:
        """Return exactly three bounded fields or raise unavailable."""

        # Only already-redacted, useful event fields cross the model boundary.
        context = {
            key: str(event.get(key, ""))
            for key in (
                "host",
                "service",
                "resource",
                "event",
                "status",
                "severity",
                "message",
            )
        }
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a terse homelab alert triage assistant. Return only "
                        "JSON with exactly what, since, and check string fields. Do not "
                        "speculate beyond the alert. Do not give destructive commands."
                    ),
                },
                {"role": "user", "content": json.dumps(context, sort_keys=True)},
            ],
            "temperature": 0.0,
            "max_tokens": 160,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "cerberus_digest_brief",
                    "schema": {
                        "type": "object",
                        "properties": {key: {"type": "string"} for key in BRIEF_FIELDS},
                        "required": list(BRIEF_FIELDS),
                        "additionalProperties": False,
                    },
                },
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                status = int(getattr(response, "status", 200))
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (OSError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise CerberusUnavailable("cerberus_unavailable") from exc
        if status < 200 or status >= 300 or len(raw) > MAX_RESPONSE_BYTES:
            raise CerberusUnavailable("cerberus_unavailable")
        try:
            outer = json.loads(raw.decode("utf-8"))
            content = outer["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise CerberusUnavailable("cerberus_unavailable") from exc
        if not isinstance(result, dict):
            raise CerberusUnavailable("cerberus_unavailable")
        bounded: dict[str, str] = {}
        for key in BRIEF_FIELDS:
            value = result.get(key)
            if not isinstance(value, str):
                raise CerberusUnavailable("cerberus_unavailable")
            value = " ".join(value.split())
            if not value or len(value) > MAX_FIELD_LENGTH:
                raise CerberusUnavailable("cerberus_unavailable")
            bounded[key] = value
        return bounded


def claim_pending(
    ledger: Ledger,
    *,
    now: float,
    lease_seconds: float = 90.0,
    limit: int = 1,
    max_attempts: int = 3,
) -> list[EnrichmentItem]:
    """Lease eligible digest enrichments and return their redacted events."""

    if limit < 1 or max_attempts < 1 or lease_seconds <= 0:
        return []
    connection = ledger.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        rows = connection.execute(
            """
            SELECT q.event_id, q.attempts, e.raw_payload_json
            FROM cerberus_enrichments AS q
            JOIN events AS e ON e.event_id = q.event_id
            WHERE q.attempts < ?
              AND (
                (q.state = 'pending' AND q.next_attempt_at <= ?)
                OR (q.state = 'leased' AND q.lease_until IS NOT NULL AND q.lease_until <= ?)
              )
            ORDER BY q.created_at ASC, q.event_id ASC
            LIMIT ?
            """,
            (max_attempts, now, now, limit),
        ).fetchall()
        items: list[EnrichmentItem] = []
        for row in rows:
            attempts = int(row["attempts"]) + 1
            connection.execute(
                """
                UPDATE cerberus_enrichments
                SET state = 'leased', attempts = ?, lease_until = ?,
                    last_error = NULL, updated_at = ?
                WHERE event_id = ?
                """,
                (attempts, now + lease_seconds, now, str(row["event_id"])),
            )
            event = json.loads(str(row["raw_payload_json"]))
            if not isinstance(event, dict):
                raise ValueError("stored event payload is not an object")
            items.append(EnrichmentItem(str(row["event_id"]), attempts, event))
        connection.execute("COMMIT")
        return items
    except Exception:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def mark_enriched(
    ledger: Ledger,
    event_id: str,
    brief: Mapping[str, str],
    *,
    now: float,
) -> None:
    """Store only the bounded brief and make the enrichment terminal."""

    clean = {}
    for key in BRIEF_FIELDS:
        value = brief.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_LENGTH:
            raise ValueError("invalid Cerberus brief")
        clean[key] = " ".join(value.split())
    connection = ledger.connection
    result = connection.execute(
        """
        UPDATE cerberus_enrichments
        SET state = 'enriched', result_json = ?, lease_until = NULL,
            last_error = NULL, enriched_at = ?, updated_at = ?
        WHERE event_id = ? AND state = 'leased'
        """,
        (compact_json(clean), now, now, event_id),
    )
    if result.rowcount != 1:
        raise ValueError("Cerberus enrichment is not currently leased")


def mark_failed(
    ledger: Ledger,
    event_id: str,
    *,
    now: float,
    retry_delay: float = 60.0,
    max_attempts: int = 3,
) -> str:
    """Retry safely, or mark failed after the bounded attempt limit."""

    connection = ledger.connection
    row = connection.execute(
        "SELECT attempts, state FROM cerberus_enrichments WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        raise KeyError(event_id)
    if row["state"] != "leased":
        raise ValueError("Cerberus enrichment is not currently leased")
    state = "failed" if int(row["attempts"]) >= max_attempts else "pending"
    result = connection.execute(
        """
        UPDATE cerberus_enrichments
        SET state = ?, next_attempt_at = ?, lease_until = NULL,
            last_error = 'cerberus_unavailable', updated_at = ?
        WHERE event_id = ? AND state = 'leased'
        """,
        (state, now + max(0.0, retry_delay), now, event_id),
    )
    if result.rowcount != 1:
        raise ValueError("Cerberus enrichment changed while failing")
    return state


def drain_once(
    ledger: Ledger,
    client: CerberusClient,
    *,
    now: float,
    lease_seconds: float = 90.0,
    limit: int = 1,
    max_attempts: int = 3,
    retry_delay: float = 60.0,
) -> DrainResult:
    """Run one bounded enrichment drain; never creates notification outbox rows."""

    items = claim_pending(
        ledger,
        now=now,
        lease_seconds=lease_seconds,
        limit=limit,
        max_attempts=max_attempts,
    )
    enriched = failed = 0
    for item in items:
        try:
            brief = client.brief(item.event)
        except Exception:
            state = mark_failed(
                ledger,
                item.event_id,
                now=now,
                retry_delay=retry_delay,
                max_attempts=max_attempts,
            )
            if state == "failed":
                failed += 1
            continue
        mark_enriched(ledger, item.event_id, brief, now=now)
        enriched += 1
    return DrainResult(len(items), enriched, failed)


def digest_briefs(ledger: Ledger, start: str, end: str) -> list[dict[str, Any]]:
    """Return enriched digest candidates without changing ledger state."""

    rows = ledger.connection.execute(
        """
        SELECT e.event_id, e.host, e.service, e.resource, e.event, e.status, e.observed_at,
               q.state, q.result_json
        FROM events AS e
        JOIN cerberus_enrichments AS q ON q.event_id = e.event_id
        WHERE e.observed_at >= ? AND e.observed_at < ?
          AND q.state = 'enriched' AND q.result_json IS NOT NULL
        ORDER BY e.observed_at ASC, e.event_id ASC
        """,
        (start, end),
    ).fetchall()
    result = []
    for row in rows:
        try:
            brief = json.loads(str(row["result_json"]))
        except json.JSONDecodeError:
            continue
        if not isinstance(brief, dict):
            continue
        result.append({
            "event_id": str(row["event_id"]),
            "host": str(row["host"]),
            "service": str(row["service"]),
            "resource": str(row["resource"]),
            "event": str(row["event"]),
            "status": str(row["status"]),
            "observed_at": str(row["observed_at"]),
            "state": str(row["state"]),
            **{key: str(brief[key]) for key in BRIEF_FIELDS if key in brief},
        })
    return result
