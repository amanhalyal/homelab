"""Verify and restore the standalone event-ledger snapshot in a tar archive."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tarfile
import tempfile
from pathlib import Path
from typing import Any

SNAPSHOT_MEMBER = "server-data/homelab-events/events.db.snapshot"
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
REQUIRED_TABLES = ("events", "incidents", "transitions", "outbox")


class ArchiveVerificationError(RuntimeError):
    """The archive cannot provide a valid standalone event snapshot."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


def _read_snapshot(archive: Path) -> bytes:
    try:
        handle = tarfile.open(archive, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveVerificationError("archive_unreadable") from exc
    try:
        try:
            member = handle.getmember(SNAPSHOT_MEMBER)
        except KeyError as exc:
            raise ArchiveVerificationError("snapshot_member_missing") from exc
        if not member.isfile():
            raise ArchiveVerificationError("snapshot_member_not_file")
        if member.size < 1 or member.size > MAX_SNAPSHOT_BYTES:
            raise ArchiveVerificationError("snapshot_size_invalid")
        extracted = handle.extractfile(member)
        if extracted is None:
            raise ArchiveVerificationError("snapshot_member_unreadable")
        data = extracted.read(MAX_SNAPSHOT_BYTES + 1)
        if len(data) != member.size or len(data) > MAX_SNAPSHOT_BYTES:
            raise ArchiveVerificationError("snapshot_size_invalid")
        return data
    except (OSError, tarfile.TarError) as exc:
        raise ArchiveVerificationError("snapshot_member_unreadable") from exc
    finally:
        handle.close()


def _validate_snapshot(path: Path) -> dict[str, Any]:
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise ArchiveVerificationError("snapshot_integrity_failed") from exc
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]) != "ok":
            raise ArchiveVerificationError("snapshot_integrity_failed")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        missing = [table for table in REQUIRED_TABLES if table not in tables]
        if missing:
            raise ArchiveVerificationError(
                "snapshot_schema_invalid", ",".join(missing)
            )
        counts = {
            table: int(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in REQUIRED_TABLES
        }
        version_row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        schema_version = str(version_row[0]) if version_row else None
        return {
            "integrity": "ok",
            "schema_version": schema_version,
            **counts,
        }
    except sqlite3.Error as exc:
        raise ArchiveVerificationError("snapshot_integrity_failed") from exc
    finally:
        connection.close()


def verify_event_archive(
    archive_path: str | Path,
    restore_path: str | Path,
) -> dict[str, Any]:
    """Restore and validate only the event snapshot from a normal archive."""

    archive = Path(archive_path).resolve()
    destination = Path(restore_path).resolve()
    if not archive.exists() or not archive.is_file():
        raise ArchiveVerificationError("archive_missing")
    if archive == destination:
        raise ArchiveVerificationError("restore_path_is_archive")
    data = _read_snapshot(archive)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        validation = _validate_snapshot(temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "ok": True,
        "archive": str(archive),
        "member": SNAPSHOT_MEMBER,
        "snapshot": str(destination),
        "snapshot_bytes": len(data),
        **validation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--restore", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(verify_event_archive(args.archive, args.restore), sort_keys=True))
    except ArchiveVerificationError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
