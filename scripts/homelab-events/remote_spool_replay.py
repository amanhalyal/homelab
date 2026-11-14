"""Replay Citadel's local event spool to the authenticated Legion intake."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ledger import validate_event
from producer_emit import emit_remote_event
from spool import EventSpool


@dataclass(frozen=True)
class RemoteReplayResult:
    attempted: int
    accepted: int
    retained: int
    invalid: int


def replay_remote_spool(
    spool_dir: str | Path,
    *,
    endpoint: str,
    secret: str | None = None,
    secret_file: str | None = None,
    timeout: float = 5.0,
    limit: int = 100,
) -> RemoteReplayResult:
    """Replay valid files and remove them only after remote acceptance."""

    if limit < 1:
        return RemoteReplayResult(0, 0, 0, 0)

    spool = EventSpool(spool_dir)
    attempted = accepted = retained = invalid = 0
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

        result = emit_remote_event(
            value,
            endpoint=endpoint,
            secret=secret,
            secret_file=secret_file,
            timeout=timeout,
            spool_dir=None,
        )
        if not result.ok:
            retained += 1
            continue
        try:
            path.unlink()
        except OSError:
            # The receiver accepted the stable event, but retain the file for
            # an idempotent retry if local cleanup failed.
            retained += 1
        else:
            accepted += 1

    return RemoteReplayResult(attempted, accepted, retained, invalid)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spool", required=True)
    parser.add_argument("--remote-endpoint", required=True)
    parser.add_argument("--remote-secret-file", required=True)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    result = replay_remote_spool(
        args.spool,
        endpoint=args.remote_endpoint,
        secret_file=args.remote_secret_file,
        timeout=args.timeout,
        limit=args.limit,
    )
    print(json.dumps(asdict(result), sort_keys=True))
    return 0 if result.retained == 0 and result.invalid == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
