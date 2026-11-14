"""SQLite schema and connection helpers for the homelab event ledger."""

from __future__ import annotations

import sqlite3
from urllib.parse import quote
from pathlib import Path

SCHEMA_VERSION = 7

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    dedupe_key TEXT NOT NULL,
    source TEXT NOT NULL,
    host TEXT NOT NULL,
    service TEXT NOT NULL,
    resource TEXT NOT NULL,
    event TEXT NOT NULL,
    status TEXT NOT NULL,
    severity TEXT NOT NULL,
    page_policy TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    message TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    inserted_at REAL NOT NULL,
    retention_state TEXT NOT NULL DEFAULT 'active',
    expired_at REAL,
    expiry_reason TEXT
);
CREATE INDEX IF NOT EXISTS events_observed_at_idx ON events(observed_at, event_id);
CREATE INDEX IF NOT EXISTS events_dedupe_key_idx ON events(dedupe_key, observed_at);

CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    cycle INTEGER NOT NULL DEFAULT 1,
    state TEXT NOT NULL,
    severity TEXT NOT NULL,
    page_policy TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    human_paged INTEGER NOT NULL DEFAULT 0 CHECK (human_paged IN (0, 1)),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS transitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    incident_id INTEGER NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE RESTRICT,
    state TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    UNIQUE(incident_id, event_id, state)
);
CREATE INDEX IF NOT EXISTS transitions_incident_idx ON transitions(incident_id, id);

CREATE TABLE IF NOT EXISTS outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    delivery_id TEXT NOT NULL UNIQUE,
    incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
    cycle INTEGER NOT NULL,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    last_error TEXT,
    created_at REAL NOT NULL,
    sent_at REAL,
    response_status INTEGER,
    response_metadata_json TEXT,
    CHECK (incident_id IS NOT NULL OR kind = 'digest'),
    UNIQUE(incident_id, cycle, kind)
);
CREATE INDEX IF NOT EXISTS outbox_claim_idx
    ON outbox(state, next_attempt_at, lease_until, attempts);

CREATE TABLE IF NOT EXISTS cerberus_enrichments (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at REAL NOT NULL DEFAULT 0,
    lease_until REAL,
    result_json TEXT,
    last_error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    enriched_at REAL
);
CREATE INDEX IF NOT EXISTS cerberus_enrichments_claim_idx
    ON cerberus_enrichments(state, next_attempt_at, lease_until, attempts);

CREATE TABLE IF NOT EXISTS cursors (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS remote_request_replays (
    request_id TEXT PRIMARY KEY,
    body_digest TEXT NOT NULL,
    signature_digest TEXT NOT NULL,
    request_timestamp INTEGER NOT NULL,
    first_seen REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS remote_request_replays_expiry_idx
    ON remote_request_replays(expires_at, request_id);
"""


def connect(
    path: str | Path,
    *,
    timeout: float = 5.0,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Open and initialise a ledger database with safe SQLite defaults."""

    path_text = str(path)
    is_new_database = path_text == ":memory:" or not Path(path_text).exists()
    if timeout < 0:
        raise ValueError("SQLite timeout must be non-negative")
    if read_only:
        if path_text == ":memory:":
            raise ValueError("read-only mode requires a database path")
        resolved = Path(path_text).resolve()
        if not resolved.exists():
            raise FileNotFoundError(resolved)
        uri = f"file:{quote(str(resolved), safe='/')}?mode=ro"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=timeout,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        return connection
    if path_text != ":memory:":
        Path(path_text).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        path_text,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    if path_text != ":memory:" and is_new_database:
        connection.execute("PRAGMA journal_mode = WAL")
    initialized = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'schema_meta'
        """
    ).fetchone()
    if is_new_database or initialized is None:
        connection.executescript(SCHEMA_SQL)
        _migrate_schema(connection)
        _ensure_retention_index(connection)
        connection.execute(
            "INSERT OR REPLACE INTO schema_meta(key, value) VALUES('version', ?)",
            (str(SCHEMA_VERSION),),
        )
    else:
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        version = int(row["value"]) if row else 1
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema version {version} is newer than supported {SCHEMA_VERSION}"
            )
        if version < SCHEMA_VERSION:
            _migrate_schema(connection)
            _ensure_retention_index(connection)
            connection.execute(
                "UPDATE schema_meta SET value = ? WHERE key = 'version'",
                (str(SCHEMA_VERSION),),
            )
    return connection


def _migrate_schema(connection: sqlite3.Connection) -> None:
    """Add prototype columns when opening an older ledger database."""

    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(events)").fetchall()
    }
    additions = (
        ("retention_state", "TEXT NOT NULL DEFAULT 'active'"),
        ("expired_at", "REAL"),
        ("expiry_reason", "TEXT"),
    )
    for name, definition in additions:
        if name not in columns:
            connection.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")

    outbox_columns = {
        row["name"]: row
        for row in connection.execute("PRAGMA table_info(outbox)").fetchall()
    }
    for name, definition in (
        ("response_status", "INTEGER"),
        ("response_metadata_json", "TEXT"),
    ):
        if name not in outbox_columns:
            connection.execute(f"ALTER TABLE outbox ADD COLUMN {name} {definition}")
    incident_column = outbox_columns.get("incident_id")
    if incident_column is not None and int(incident_column["notnull"]) == 1:
        _rebuild_outbox_for_digest(connection)

    _ensure_remote_replay_table(connection)
    _ensure_cerberus_table(connection)


def _ensure_remote_replay_table(connection: sqlite3.Connection) -> None:
    """Create the remote replay table when migrating an older ledger."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS remote_request_replays (
            request_id TEXT PRIMARY KEY,
            body_digest TEXT NOT NULL,
            signature_digest TEXT NOT NULL,
            request_timestamp INTEGER NOT NULL,
            first_seen REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS remote_request_replays_expiry_idx
            ON remote_request_replays(expires_at, request_id);
        """
    )


def _ensure_cerberus_table(connection: sqlite3.Connection) -> None:
    """Create the optional digest-enrichment queue during migration."""

    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS cerberus_enrichments (
            event_id TEXT PRIMARY KEY REFERENCES events(event_id) ON DELETE CASCADE,
            state TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at REAL NOT NULL DEFAULT 0,
            lease_until REAL,
            result_json TEXT,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            enriched_at REAL
        );
        CREATE INDEX IF NOT EXISTS cerberus_enrichments_claim_idx
            ON cerberus_enrichments(state, next_attempt_at, lease_until, attempts);
        """
    )


def _rebuild_outbox_for_digest(connection: sqlite3.Connection) -> None:
    """Make digest intents independent of an incident while preserving rows."""

    connection.execute("BEGIN")
    try:
        connection.execute("DROP INDEX IF EXISTS outbox_claim_idx")
        connection.execute("ALTER TABLE outbox RENAME TO outbox_v3")
        connection.execute(
            """
            CREATE TABLE outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_id TEXT NOT NULL UNIQUE,
                incident_id INTEGER REFERENCES incidents(id) ON DELETE CASCADE,
                cycle INTEGER NOT NULL,
                kind TEXT NOT NULL,
                topic TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                lease_until REAL,
                last_error TEXT,
                created_at REAL NOT NULL,
                sent_at REAL,
                response_status INTEGER,
                response_metadata_json TEXT,
                CHECK (incident_id IS NOT NULL OR kind = 'digest'),
                UNIQUE(incident_id, cycle, kind)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO outbox(
                id, delivery_id, incident_id, cycle, kind, topic, payload_json,
                state, attempts, next_attempt_at, lease_until, last_error,
                created_at, sent_at, response_status, response_metadata_json
            )
            SELECT id, delivery_id, incident_id, cycle, kind, topic, payload_json,
                   state, attempts, next_attempt_at, lease_until, last_error,
                   created_at, sent_at, response_status, response_metadata_json
            FROM outbox_v3
            """
        )
        connection.execute("DROP TABLE outbox_v3")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS outbox_claim_idx
            ON outbox(state, next_attempt_at, lease_until, attempts)
            """
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _ensure_retention_index(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS events_retention_idx
        ON events(retention_state, expired_at, event_id)
        """
    )


def integrity_check(connection: sqlite3.Connection) -> str:
    """Return SQLite's integrity-check result."""

    row = connection.execute("PRAGMA integrity_check").fetchone()
    return str(row[0]) if row else ""
