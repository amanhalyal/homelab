"""Helpers shared by more than one part of the event pipeline.

Kept small and dependency-free. Each function reproduces what several modules
used to carry as private copies, so an event ID, a digest or a spool file is
byte-for-byte what it was before.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def canonical_json(value: Any) -> str:
    """Stable, compact JSON that keeps non-ASCII text readable.

    Used wherever the text itself is stored, hashed or compared.
    """

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compact_json(value: Any) -> str:
    """Stable, compact, ASCII-safe JSON for transport and database columns."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_hex(value: str | bytes) -> str:
    """Hex SHA-256 of text (UTF-8) or bytes."""

    data = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(data).hexdigest()


def iso_z(moment: datetime) -> str:
    """An aware datetime as UTC ISO-8601 with a trailing Z."""

    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_now_iso() -> str:
    """The current UTC time to whole seconds, as ISO-8601 with a trailing Z."""

    return iso_z(datetime.now(timezone.utc).replace(microsecond=0))


def atomic_write_text(
    destination: str | Path,
    text: str,
    *,
    mode: int = 0o600,
    prefix: str = ".tmp-",
) -> None:
    """Write text so a reader sees the old file or the new one, never half.

    The data is fsynced before the rename, and the temporary file is removed on
    any failure.
    """

    destination = Path(destination)
    fd, temporary_name = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
