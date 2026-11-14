"""Transactional outbox leasing and bounded retry operations."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class OutboxItem:
    id: int
    delivery_id: str
    incident_id: int | None
    cycle: int
    kind: str
    topic: str
    payload: dict[str, Any]
    attempts: int
    state: str


class Outbox:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def claim(
        self,
        *,
        now: float,
        lease_seconds: float = 60.0,
        limit: int = 10,
        max_attempts: int = 5,
    ) -> list[OutboxItem]:
        """Lease eligible rows; expired leases are safely reclaimable."""

        if limit < 1:
            return []
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            rows = connection.execute(
                """
                SELECT * FROM outbox
                WHERE attempts < ?
                  AND (
                    (state = 'pending' AND next_attempt_at <= ?)
                    OR (state = 'leased' AND lease_until IS NOT NULL AND lease_until <= ?)
                  )
                ORDER BY id ASC
                LIMIT ?
                """,
                (max_attempts, now, now, limit),
            ).fetchall()
            items = []
            for row in rows:
                attempts = int(row["attempts"]) + 1
                connection.execute(
                    """
                    UPDATE outbox
                    SET state = 'leased', attempts = ?, lease_until = ?, last_error = NULL
                    WHERE id = ?
                    """,
                    (attempts, now + lease_seconds, int(row["id"])),
                )
                items.append(
                    OutboxItem(
                        id=int(row["id"]),
                        delivery_id=str(row["delivery_id"]),
                        incident_id=(
                            int(row["incident_id"])
                            if row["incident_id"] is not None
                            else None
                        ),
                        cycle=int(row["cycle"]),
                        kind=str(row["kind"]),
                        topic=str(row["topic"]),
                        payload=json.loads(row["payload_json"]),
                        attempts=attempts,
                        state="leased",
                    )
                )
            connection.execute("COMMIT")
            return items
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def mark_sent(
        self,
        delivery_id: str,
        *,
        sent_at: float,
        response_status: int | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            result = connection.execute(
                """
                UPDATE outbox
                SET state = 'sent', sent_at = ?, lease_until = NULL,
                    last_error = NULL, response_status = ?, response_metadata_json = ?
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
                    delivery_id,
                ),
            )
            if result.rowcount != 1:
                raise ValueError("delivery is not currently leased")
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def fail(
        self,
        delivery_id: str,
        error: str,
        *,
        next_attempt_at: float,
        max_attempts: int = 5,
        response_status: int | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> str:
        """Record failure and return the resulting state."""

        connection = self.connection
        connection.execute("BEGIN IMMEDIATE")
        try:
            row = connection.execute(
                "SELECT attempts, state FROM outbox WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(delivery_id)
            if row["state"] not in {"leased", "pending"}:
                raise ValueError("delivery is already terminal")
            state = "failed" if int(row["attempts"]) >= max_attempts else "pending"
            connection.execute(
                """
                UPDATE outbox
                SET state = ?, next_attempt_at = ?, lease_until = NULL, last_error = ?,
                    response_status = ?, response_metadata_json = ?
                WHERE delivery_id = ?
                """,
                (
                    state,
                    next_attempt_at,
                    str(error),
                    response_status,
                    (
                        json.dumps(response_metadata, sort_keys=True, separators=(",", ":"))
                        if response_metadata is not None
                        else None
                    ),
                    delivery_id,
                ),
            )
            connection.execute("COMMIT")
            return state
        except Exception:
            connection.execute("ROLLBACK")
            raise

    def get(self, delivery_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM outbox WHERE delivery_id = ?", (delivery_id,)
        ).fetchone()
