"""Local, durable JSON spool for events awaiting the Legion ledger."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from homelab_events.store.ledger import ControlPlaneError, Ledger, redact_event, validate_event
from homelab_events.common import atomic_write_text, canonical_json, sha256_hex


class SpoolConflict(ValueError):
    """The same event ID was spooled with different content."""


@dataclass(frozen=True)
class ReplayResult:
    attempted: int
    accepted: int
    retained: int
    invalid: int


class EventSpool:
    """Filesystem spool with atomic writes and replay-safe deletion."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path_for(self, event_id: str) -> Path:
        digest = sha256_hex(event_id)
        return self.root / f"{digest}.json"

    def append(self, event: dict[str, Any]) -> bool:
        """Append an event, returning false for an identical retry."""

        validate_event(event)
        safe_event = redact_event(event)
        serialized = canonical_json(safe_event)
        destination = self._path_for(str(safe_event["event_id"]))
        if destination.exists():
            existing = destination.read_text(encoding="utf-8").rstrip("\n")
            if existing == serialized:
                return False
            raise SpoolConflict(str(safe_event["event_id"]))

        atomic_write_text(destination, serialized + "\n", prefix=".event-")
        return True

    def files(self) -> list[Path]:
        """Return pending JSON files in deterministic order."""

        return sorted(self.root.glob("*.json"), key=lambda path: path.name)

    def replay(self, ledger: Ledger, *, limit: int = 100) -> ReplayResult:
        """Replay pending files; retain every event not durably accepted."""

        if limit < 1:
            return ReplayResult(attempted=0, accepted=0, retained=0, invalid=0)
        attempted = accepted = retained = invalid = 0
        for path in self.files()[:limit]:
            attempted += 1
            try:
                event = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(event, dict):
                    raise ValueError("spooled event must be an object")
                validate_event(event)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                invalid += 1
                continue
            try:
                ledger.ingest(event)
            except ControlPlaneError:
                retained += 1
                continue
            except Exception:
                retained += 1
                continue
            path.unlink()
            accepted += 1
        return ReplayResult(
            attempted=attempted,
            accepted=accepted,
            retained=retained,
            invalid=invalid,
        )
