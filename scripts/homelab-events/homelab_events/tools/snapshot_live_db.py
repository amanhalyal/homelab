"""Create a standalone online snapshot of the live event ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from homelab_events.store.backup import backup_database


def snapshot_database(source: str | Path, destination: str | Path) -> Path | None:
    """Snapshot a live SQLite database, or no-op before the first event."""

    source_path = Path(source)
    if not source_path.exists():
        return None
    return backup_database(source_path, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default="/opt/homelab/data/homelab-events/events.db")
    parser.add_argument("--destination", default="/opt/homelab/data/homelab-events/events.db.snapshot")
    args = parser.parse_args(argv)
    result = snapshot_database(args.source, args.destination)
    if result is None:
        print(json.dumps({"ok": True, "reason": "source_absent", "snapshot": None}, sort_keys=True))
    else:
        print(json.dumps({"ok": True, "reason": "snapshot_created", "snapshot": str(result)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
