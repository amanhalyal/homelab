"""Read-only view of the incidents that are open in the SQLite event ledger."""

from __future__ import annotations

from typing import Any

from ledger import Ledger


def open_incidents(ledger: Ledger) -> list[dict[str, Any]]:
    rows = ledger.connection.execute(
        """
        SELECT i.*, e.host, e.service, e.resource, e.event, e.message,
               e.observed_at
        FROM incidents AS i
        JOIN events AS e ON e.dedupe_key = i.dedupe_key
        WHERE i.state = 'open'
          AND e.event_id = (
              SELECT e2.event_id FROM events AS e2
              WHERE e2.dedupe_key = i.dedupe_key
              ORDER BY e2.observed_at DESC, e2.event_id DESC
              LIMIT 1
          )
        ORDER BY e.service ASC, e.host ASC, i.dedupe_key ASC
        """
    ).fetchall()
    return [
        {
            "incident_id": int(row["id"]),
            "dedupe_key": str(row["dedupe_key"]),
            "cycle": int(row["cycle"]),
            "state": str(row["state"]),
            "severity": str(row["severity"]),
            "page_policy": str(row["page_policy"]),
            "occurrence_count": int(row["occurrence_count"]),
            "first_seen": str(row["first_seen"]),
            "host": str(row["host"]),
            "service": str(row["service"]),
            "resource": str(row["resource"]),
            "event": str(row["event"]),
            "observed_at": str(row["observed_at"]),
            "message": str(row["message"]),
        }
        for row in rows
    ]
