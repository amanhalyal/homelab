"""Transactional outbox worker with an explicit production-delivery guard."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError

from digest_delivery import mark_digest_sent
from ledger import Ledger
from notify_gate import load_policy
from outbox import Outbox, OutboxItem


class Transport(Protocol):
    def send(self, item: OutboxItem) -> "TransportReceipt | None":
        """Deliver one committed notification intent."""


@dataclass(frozen=True)
class WorkerResult:
    claimed: int
    sent: int
    failed: int
    failures: tuple[str, ...] = ()
    response_statuses: tuple[int, ...] = ()


@dataclass(frozen=True)
class TransportReceipt:
    """Safe, bounded evidence returned by a notification transport."""

    status: int | None
    metadata: dict[str, Any] = field(default_factory=dict)


class TransportError(RuntimeError):
    """A transport failure with non-secret response evidence, when available."""

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.metadata = dict(metadata or {})


_RESPONSE_BODY_KEYS = frozenset({"id", "time", "expires", "topic", "title", "priority", "tags"})


def _response_metadata(response: Any, body: bytes) -> dict[str, Any]:
    """Keep only bounded, non-secret ntfy response metadata."""

    metadata: dict[str, Any] = {}
    content_type = response.headers.get("Content-Type")
    if content_type:
        metadata["content_type"] = str(content_type).split(";", 1)[0].strip()
    if not body:
        return metadata
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        metadata["body_bytes"] = len(body)
        return metadata
    if isinstance(decoded, dict):
        for key in _RESPONSE_BODY_KEYS:
            if key in decoded and isinstance(decoded[key], (bool, int, float, str, list)):
                metadata[key] = decoded[key]
    else:
        metadata["body_bytes"] = len(body)
    return metadata


class NtfyTransport:
    """Minimal ntfy transport; credentials never enter logs or payloads."""

    def __init__(
        self,
        base_url: str,
        *,
        username: str,
        password: str,
        timeout: float = 10.0,
        allow_insecure_local: bool = False,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("ntfy base URL must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and not allow_insecure_local:
            raise ValueError("ntfy transport requires HTTPS unless local HTTP is explicit")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.timeout = timeout
        self.allow_insecure_local = allow_insecure_local

    def send(self, item: OutboxItem) -> TransportReceipt:
        topic = urllib.parse.quote(item.topic, safe="")
        url = f"{self.base_url}/{topic}"
        message = item.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise TransportError("notification payload is missing a message")
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        title = item.payload.get("title")
        if isinstance(title, str) and title.strip():
            headers["Title"] = title
        priority = {
            "critical": "max",
            "warning": "default",
            "info": "min",
        }.get(str(item.payload.get("severity", "")).lower())
        if priority:
            headers["Priority"] = priority
        body = message.encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers=headers,
        )
        if self.username or self.password:
            token = base64.b64encode(
                f"{self.username}:{self.password}".encode("utf-8")
            ).decode("ascii")
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(response.status)
                body = response.read(64 * 1024)
                metadata = _response_metadata(response, body)
        except HTTPError as error:
            metadata: dict[str, Any] = {}
            content_type = error.headers.get("Content-Type") if error.headers else None
            if content_type:
                metadata["content_type"] = str(content_type).split(";", 1)[0].strip()
            raise TransportError(
                f"ntfy returned HTTP {int(error.code)}",
                status=int(error.code),
                metadata=metadata,
            ) from error
        if not 200 <= status < 300:
            raise TransportError(
                f"ntfy returned HTTP {status}",
                status=status,
                metadata=metadata,
            )
        return TransportReceipt(status=status, metadata=metadata)


def drain_once(
    ledger: Ledger,
    transport: Transport,
    *,
    now: float,
    lease_seconds: float = 60.0,
    limit: int = 10,
    max_attempts: int = 5,
    retry_delay: float = 300.0,
) -> WorkerResult:
    """Deliver eligible intents once with bounded at-least-once semantics."""

    outbox = Outbox(ledger.connection)
    items = outbox.claim(
        now=now,
        lease_seconds=lease_seconds,
        limit=limit,
        max_attempts=max_attempts,
    )
    sent = 0
    failed = 0
    failures: list[str] = []
    response_statuses: list[int] = []
    for item in items:
        try:
            receipt = transport.send(item)
        except TransportError as exc:
            failed += 1
            message = str(exc) or exc.__class__.__name__
            failures.append(f"{item.delivery_id}: {message}")
            outbox.fail(
                item.delivery_id,
                message,
                next_attempt_at=now + retry_delay,
                max_attempts=max_attempts,
                response_status=exc.status,
                response_metadata=exc.metadata,
            )
        except Exception as exc:  # transport failures are retryable/observable
            failed += 1
            message = str(exc) or exc.__class__.__name__
            failures.append(f"{item.delivery_id}: {message}")
            outbox.fail(
                item.delivery_id,
                message,
                next_attempt_at=now + retry_delay,
                max_attempts=max_attempts,
            )
        else:
            response_status = getattr(receipt, "status", None)
            response_metadata = getattr(receipt, "metadata", None)
            if response_status is not None:
                response_statuses.append(int(response_status))
            if item.kind == "digest":
                mark_digest_sent(
                    ledger,
                    item,
                    sent_at=now,
                    response_status=response_status,
                    response_metadata=response_metadata,
                )
            else:
                outbox.mark_sent(
                    item.delivery_id,
                    sent_at=now,
                    response_status=response_status,
                    response_metadata=response_metadata,
                )
            sent += 1
    return WorkerResult(
        claimed=len(items),
        sent=sent,
        failed=failed,
        failures=tuple(failures),
        response_statuses=tuple(response_statuses),
    )


def _pending_snapshot(ledger: Ledger) -> dict[str, Any]:
    rows = ledger.connection.execute(
        """
        SELECT delivery_id, incident_id, cycle, kind, topic, state, attempts
        FROM outbox
        WHERE state IN ('pending', 'leased')
        ORDER BY id ASC
        """
    ).fetchall()
    return {
        "pending": len(rows),
        "items": [dict(row) for row in rows],
    }


def _production_enabled(policy_path: str, *, override: bool) -> bool:
    policy = load_policy(policy_path)
    return policy.production_enabled and override and os.environ.get(
        "HOMELAB_EVENTS_ENABLE_DELIVERY"
    ) == "1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/homelab-events/events.db")
    parser.add_argument("--policy", default="scripts/homelab-events/policy.yaml")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--now", type=float)
    parser.add_argument("--send", action="store_true")
    parser.add_argument(
        "--enable-production-delivery",
        action="store_true",
        help="second explicit guard; policy and environment must also enable delivery",
    )
    parser.add_argument("--base-url", default=os.environ.get("NTFY_BASE_URL", ""))
    parser.add_argument("--username", default=os.environ.get("NTFY_USER", ""))
    parser.add_argument("--password", default=os.environ.get("NTFY_PASSWORD", ""))
    parser.add_argument("--allow-insecure-local", action="store_true")
    args = parser.parse_args(argv)

    if not args.send:
        ledger = Ledger(args.db, timeout=args.timeout, read_only=True)
        try:
            print(json.dumps(_pending_snapshot(ledger), sort_keys=True))
        finally:
            ledger.close()
        return 0

    if not _production_enabled(
        args.policy, override=args.enable_production_delivery
    ):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "production_delivery_disabled",
                    "detail": "policy, CLI override, and HOMELAB_EVENTS_ENABLE_DELIVERY=1 are required",
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 78
    if not args.base_url:
        print(
            json.dumps(
                {"ok": False, "error": "ntfy_base_url_required"}, sort_keys=True
            ),
            file=sys.stderr,
        )
        return 78

    transport = NtfyTransport(
        args.base_url,
        username=args.username,
        password=args.password,
        timeout=args.timeout,
        allow_insecure_local=args.allow_insecure_local,
    )
    ledger = Ledger(args.db, timeout=args.timeout)
    try:
        result = drain_once(
            ledger,
            transport,
            now=args.now if args.now is not None else __import__("time").time(),
            limit=args.limit,
        )
    finally:
        ledger.close()
    print(json.dumps(result.__dict__, sort_keys=True))
    return 0 if result.failed == 0 else 75


if __name__ == "__main__":
    raise SystemExit(main())
