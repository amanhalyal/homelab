"""Durable daily-digest rendering and outbox/cursor coordination."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from ledger import Ledger
from outbox import OutboxItem


@dataclass(frozen=True)
class DigestEnqueueResult:
    created: bool
    delivery_id: str
    digest: dict[str, Any]


def _delivery_id(start: str, end: str) -> str:
    material = f"digest\0{start}\0{end}".encode("utf-8")
    return "digest-" + hashlib.sha256(material).hexdigest()


def enqueue_rendered_digest(
    ledger: Ledger,
    start: str,
    end: str,
    *,
    message: str,
    now: float,
    topic: str = "homelab-digest",
    title: str = "Homelab daily digest",
    counts: Mapping[str, Any] | None = None,
    digest: Mapping[str, Any] | None = None,
) -> DigestEnqueueResult:
    """Enqueue an already-rendered digest without advancing its cursor.

    The daily health report is composed by the digest script. This helper puts
    it on the durable outbox/cursor path so delivery is retried and the cursor
    advances only after the send succeeds.
    """

    if not isinstance(start, str) or not start:
        raise ValueError("digest start must be a non-empty string")
    if not isinstance(end, str) or not end:
        raise ValueError("digest end must be a non-empty string")
    if not isinstance(message, str) or not message:
        raise ValueError("digest message must be a non-empty string")
    delivery_id = _delivery_id(start, end)
    digest_value = dict(digest or {"window": {"start": start, "end": end}})
    payload = {
        "delivery_id": delivery_id,
        "kind": "digest",
        "topic": topic,
        "window": {"start": start, "end": end},
        "cursor": end,
        "title": title,
        "message": message,
        "counts": dict(counts or {}),
    }
    connection = ledger.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = connection.execute(
            """
            INSERT OR IGNORE INTO outbox(
                delivery_id, incident_id, cycle, kind, topic, payload_json,
                state, attempts, next_attempt_at, created_at
            ) VALUES (?, NULL, 0, 'digest', ?, ?, 'pending', 0, 0, ?)
            """,
            (delivery_id, topic, json.dumps(payload, sort_keys=True, separators=(",", ":")), now),
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
    return DigestEnqueueResult(result.rowcount == 1, delivery_id, digest_value)


def mark_digest_sent(
    ledger: Ledger,
    item: OutboxItem,
    *,
    sent_at: float,
    response_status: int | None = None,
    response_metadata: dict[str, Any] | None = None,
) -> None:
    """Mark a digest sent and advance its cursor in one SQLite transaction."""

    if item.kind != "digest":
        raise ValueError("mark_digest_sent requires a digest outbox item")
    cursor_value = item.payload.get("cursor")
    if not isinstance(cursor_value, str) or not cursor_value:
        raise ValueError("digest payload is missing its cursor")
    connection = ledger.connection
    connection.execute("BEGIN IMMEDIATE")
    try:
        result = connection.execute(
            """
            UPDATE outbox
            SET state = 'sent', sent_at = ?, lease_until = NULL, last_error = NULL,
                response_status = ?, response_metadata_json = ?
            WHERE delivery_id = ? AND state = 'leased'
            """,
            (
                sent_at,
                response_status,
                (
                    json.dumps(response_metadata, sort_keys=True, separators=(",", ":"))
                    if response_metadata is not None
                    else None
                ),
                item.delivery_id,
            ),
        )
        if result.rowcount != 1:
            raise ValueError("digest delivery is not currently leased")
        connection.execute(
            """
            INSERT INTO cursors(name, value, updated_at)
            VALUES ('digest.last_success_timestamp', ?, ?)
            ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (cursor_value, sent_at),
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise
