"""Replay locally spooled producer events through canonical policy intake."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from homelab_events.intake.canonical_intake import ingest_event
from homelab_events.store.ledger import Ledger, validate_event
from homelab_events.policy.notify_gate import load_policy
from homelab_events.producers.spool import EventSpool


@dataclass(frozen=True)
class LocalReplayResult:
    attempted: int
    accepted: int
    retained: int
    invalid: int


def replay_local_spool(
    spool_dir: str | Path,
    *,
    db_path: str | Path,
    policy_path: str | Path,
    timeout: float = 5.0,
    limit: int = 100,
) -> LocalReplayResult:
    """Replay valid files and remove them only after policy intake accepts them."""

    if limit < 1:
        return LocalReplayResult(0, 0, 0, 0)
    spool = EventSpool(spool_dir)
    policy = load_policy(policy_path)
    ledger = Ledger(db_path, timeout=timeout, critical_topic=policy.critical_topic)
    attempted = accepted = retained = invalid = 0
    try:
        for path in spool.files()[:limit]:
            attempted += 1
            try:
                value: Any = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(value, dict):
                    raise ValueError("spooled event must be an object")
                validate_event(value)
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                invalid += 1
                continue

            try:
                result = ingest_event(value, ledger, spool, policy)
            except Exception:
                retained += 1
                continue
            if result.spooled:
                retained += 1
                continue
            try:
                path.unlink()
            except OSError:
                retained += 1
            else:
                accepted += 1
    finally:
        ledger.close()
    return LocalReplayResult(attempted, accepted, retained, invalid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    result = replay_local_spool(
        args.spool,
        db_path=args.db,
        policy_path=args.policy,
        timeout=args.timeout,
        limit=args.limit,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.retained == 0 and result.invalid == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
