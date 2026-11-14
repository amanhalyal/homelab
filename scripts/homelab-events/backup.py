"""Atomic SQLite online backup and restore helpers."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from schema import connect


def _online_backup(source_path: str | Path, destination_path: str | Path) -> Path:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    if source == destination:
        raise ValueError("source and destination databases must differ")
    if not source.exists():
        raise FileNotFoundError(source)

    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(fd)
    temporary = Path(temporary_name)
    source_connection = None
    destination_connection = None
    try:
        source_connection = connect(source, read_only=True)
        destination_connection = sqlite3.connect(
            str(temporary),
            isolation_level=None,
            check_same_thread=False,
        )
        source_connection.backup(destination_connection)
        check = destination_connection.execute("PRAGMA integrity_check").fetchone()
        if not check or str(check[0]) != "ok":
            raise sqlite3.DatabaseError("backup integrity check failed")
        destination_connection.close()
        destination_connection = None
        source_connection.close()
        source_connection = None
        os.replace(temporary, destination)
        return destination
    finally:
        if destination_connection is not None:
            destination_connection.close()
        if source_connection is not None:
            source_connection.close()
        temporary.unlink(missing_ok=True)


def backup_database(source_path: str | Path, destination_path: str | Path) -> Path:
    """Create an atomic, consistent backup of a live SQLite database."""

    return _online_backup(source_path, destination_path)


def restore_database(backup_path: str | Path, destination_path: str | Path) -> Path:
    """Restore a database from a previously verified SQLite backup."""

    return _online_backup(backup_path, destination_path)
