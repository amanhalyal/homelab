#!/usr/bin/env python3
"""Drain pending Cerberus digest enrichments, starting the model only when needed.

Runs from a user systemd timer. The policy decision has already been recorded
in the ledger, so nothing here can delay, suppress or decide a critical page.
It fails open: if the model cannot start or answer, events stay pending (and
expire after a day) and the digest simply has no brief for them.

The model is on-demand to keep the small GPU free. This runner starts it only
when there is eligible work, drains a bounded batch, and stops it again if it
was the one that started it.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from cerberus_enrichment import CerberusClient, DEFAULT_ENDPOINT, drain_once
from ledger import Ledger

SERVICE = "llama-worker.service"
HEALTH_URL = "http://127.0.0.1:8081/health"
STATE_FILE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "cerberus-drain.json"


def expire_stale(ledger: Ledger, *, now: float, max_age_hours: float) -> int:
    """Stop pursuing enrichments too old to be useful in a digest."""

    cutoff = now - max_age_hours * 3600
    result = ledger.connection.execute(
        """
        UPDATE cerberus_enrichments
        SET state = 'failed', last_error = 'expired', lease_until = NULL, updated_at = ?
        WHERE state IN ('pending', 'leased') AND created_at < ?
        """,
        (now, cutoff),
    )
    return int(result.rowcount or 0)


def eligible_count(ledger: Ledger, *, now: float, max_attempts: int) -> int:
    row = ledger.connection.execute(
        """
        SELECT count(*) FROM cerberus_enrichments
        WHERE attempts < ?
          AND ((state = 'pending' AND next_attempt_at <= ?)
               OR (state = 'leased' AND lease_until IS NOT NULL AND lease_until <= ?))
        """,
        (max_attempts, now, now),
    ).fetchone()
    return int(row[0])


def model_healthy(url: str = HEALTH_URL, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= int(response.status) < 300
    except (OSError, urllib.error.URLError, ValueError):
        return False


def systemctl(action: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "--user", action, SERVICE],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def start_model(
    *,
    wait_seconds: float,
    healthy: Callable[[], bool] = model_healthy,
    start: Callable[[], bool] = lambda: systemctl("start"),
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    if not start():
        return False
    deadline = monotonic() + wait_seconds
    while monotonic() < deadline:
        if healthy():
            return True
        sleep(2.0)
    return False


def read_state(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, sort_keys=True))
    except OSError:
        pass


def run(
    ledger: Ledger,
    client: CerberusClient,
    *,
    now: float,
    state_path: Path = STATE_FILE,
    max_age_hours: float = 36.0,
    max_attempts: int = 3,
    batch_limit: int = 20,
    time_budget: float = 600.0,
    start_wait: float = 90.0,
    start_backoff: float = 3600.0,
    healthy: Callable[[], bool] = model_healthy,
    start: Callable[[], bool] = lambda: systemctl("start"),
    stop: Callable[[], bool] = lambda: systemctl("stop"),
    monotonic: Callable[[], float] = time.monotonic,
    clock: Callable[[], float] = time.time,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "expired": expire_stale(ledger, now=now, max_age_hours=max_age_hours),
        "eligible": 0,
        "enriched": 0,
        "failed": 0,
        "model": "not_needed",
    }
    summary["eligible"] = eligible_count(ledger, now=now, max_attempts=max_attempts)
    if summary["eligible"] == 0:
        return summary

    started_here = False
    if healthy():
        summary["model"] = "already_running"
    else:
        state = read_state(state_path)
        if now - float(state.get("last_start_failure", 0)) < start_backoff:
            summary["model"] = "start_backoff"
            return summary
        if not start_model(wait_seconds=start_wait, healthy=healthy, start=start):
            write_state(state_path, {**state, "last_start_failure": now})
            summary["model"] = "start_failed"
            stop()  # do not leave a half-started container holding GPU memory
            return summary
        started_here = True
        summary["model"] = "started"

    try:
        deadline = monotonic() + time_budget
        consecutive_failures = 0
        for _ in range(batch_limit):
            if monotonic() >= deadline:
                break
            result = drain_once(
                ledger, client, now=clock(), limit=1, max_attempts=max_attempts,
            )
            if result.claimed == 0:
                break
            summary["enriched"] += result.enriched
            summary["failed"] += result.failed
            if result.enriched == 0:
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    break  # the model is not answering; leave the rest pending
            else:
                consecutive_failures = 0
    finally:
        if started_here:
            stop()
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/homelab-events/events.db")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--max-age-hours", type=float, default=36.0)
    parser.add_argument("--batch-limit", type=int, default=20)
    parser.add_argument("--time-budget", type=float, default=600.0)
    args = parser.parse_args(argv)

    ledger = Ledger(args.db, timeout=10.0)
    try:
        summary = run(
            ledger,
            CerberusClient(endpoint=args.endpoint),
            now=time.time(),
            max_age_hours=args.max_age_hours,
            batch_limit=args.batch_limit,
            time_budget=args.time_budget,
        )
    except Exception as exc:  # fail open: a broken enrichment must never page or crash a timer loop
        print(json.dumps({"error": type(exc).__name__}))
        return 0
    finally:
        ledger.close()
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
