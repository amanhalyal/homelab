"""Internal HTTP receiver that records Alertmanager, Uptime Kuma and remote events in the ledger."""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from homelab_events.intake.alertmanager_adapter import adapt_alertmanager_payload
from homelab_events.intake.canonical_intake import CanonicalIntakeResult, gate_event, ingest_event
from homelab_events.store.ledger import ControlPlaneError, Ledger
from homelab_events.store.metrics import render_prometheus
from homelab_events.policy.notify_gate import PolicyConfig, load_policy
from homelab_events.intake.remote_auth import AuthError, verify_payload
from homelab_events.intake.shadow_intake import IntakeResult, ingest_alertmanager
from homelab_events.producers.spool import EventSpool
from homelab_events.intake.uptime_kuma_adapter import adapt_uptime_kuma_payload
from homelab_events.common import compact_json

MAX_BODY_BYTES = 1024 * 1024


class ShadowReceiver:
    """Persist Alertmanager events locally, spooling on ledger failure."""

    def __init__(
        self,
        *,
        db_path: str | Path = "/var/lib/homelab-events/events.db",
        spool_dir: str | Path = "/var/lib/homelab-events/spool",
        ledger: Any | None = None,
        spool: EventSpool | None = None,
        policy: PolicyConfig | None = None,
        remote_secret: str | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self._ledger = ledger
        self.spool = spool or EventSpool(spool_dir)
        self.policy = policy
        self.remote_secret = remote_secret
        self._ledger_lock = threading.RLock()

    def ingest(self, payload: Mapping[str, Any]) -> IntakeResult:
        """Normalize and durably accept a webhook without human delivery."""

        with self._ledger_lock:
            # Validate/normalize before trying to open the database so malformed
            # requests are never hidden behind a store initialization failure.
            events = adapt_alertmanager_payload(payload)
            if self._ledger is None:
                try:
                    self._ledger = Ledger(self.db_path)
                except Exception:
                    # The payload is already validated and redacted by the spool;
                    # retain it locally if the ledger cannot even be initialized.
                    for event in events:
                        if self.policy is not None:
                            event, _ = gate_event(event, self.policy)
                        self.spool.append(event)
                    return IntakeResult(
                        events=len(events), accepted=0,
                        spooled=len(events), duplicates=0,
                    )
            return ingest_alertmanager(
                payload,
                self._ledger,
                self.spool,
                policy=self.policy,
            )

    def ingest_canonical(self, event: Mapping[str, Any]) -> CanonicalIntakeResult:
        """Persist one producer event through the shadow policy boundary."""

        with self._ledger_lock:
            if self.policy is None:
                raise ValueError("canonical producer intake requires a policy")
            if self._ledger is None:
                safe_event, decision = gate_event(event, self.policy)
                try:
                    self._ledger = Ledger(self.db_path)
                except Exception:
                    self.spool.append(safe_event)
                    return CanonicalIntakeResult(
                        event=safe_event,
                        decision=decision,
                        ingest=None,
                        spooled=True,
                    )
            return ingest_event(event, self._ledger, self.spool, self.policy)

    def ingest_uptime_kuma(self, payload: Mapping[str, Any]) -> IntakeResult:
        """Normalize one Kuma webhook and persist it through canonical intake."""

        events = adapt_uptime_kuma_payload(payload)
        accepted = spooled = duplicates = 0
        for event in events:
            result = self.ingest_canonical(event)
            if result.spooled:
                spooled += 1
            else:
                accepted += 1
                if result.ingest is not None and result.ingest.duplicate:
                    duplicates += 1
        return IntakeResult(
            events=len(events),
            accepted=accepted,
            spooled=spooled,
            duplicates=duplicates,
        )

    def claim_remote_request(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        """Claim a verified remote request before canonical ingestion."""

        with self._ledger_lock:
            if self._ledger is None:
                self._ledger = Ledger(self.db_path)
            claim = getattr(self._ledger, "claim_remote_request", None)
            if claim is None:
                raise ControlPlaneError(
                    "remote_replay_unsupported",
                    "ledger does not support persistent remote replay protection",
                )
            return bool(claim(body, timestamp, signature))

    def close(self) -> None:
        with self._ledger_lock:
            close = getattr(self._ledger, "close", None)
            if close is not None:
                close()

    def metrics_text(self) -> str:
        """Render a read-only Prometheus snapshot for the local ledger."""
        with self._ledger_lock:
            ledger = self._ledger
            temporary = False
            if ledger is None:
                ledger = Ledger(self.db_path, read_only=True)
                temporary = True
            try:
                return render_prometheus(ledger.metrics())
            finally:
                if temporary:
                    ledger.close()


def _json_body(handler: BaseHTTPRequestHandler, status: int, body: Mapping[str, Any]) -> None:
    encoded = compact_json(body).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(encoded)


def _text_body(handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
    encoded = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(encoded)


def create_server(
    receiver: ShadowReceiver,
    host: str = "127.0.0.1",
    port: int = 8088,
) -> ThreadingHTTPServer:
    """Create, but do not start, the internal shadow HTTP server."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "homelab-event-shadow/1"

        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                _json_body(self, 200, {"status": "ok"})
            elif path == "/metrics":
                try:
                    body = receiver.metrics_text()
                except Exception:
                    _json_body(self, 503, {"error": "shadow_metrics_unavailable"})
                else:
                    _text_body(self, 200, body)
            else:
                _json_body(self, 404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path not in {"/alertmanager", "/event", "/remote-event", "/uptime-kuma"}:
                _json_body(self, 404, {"error": "not_found"})
                return

            content_length = self.headers.get("Content-Length")
            try:
                length = int(content_length) if content_length is not None else -1
            except ValueError:
                length = -1
            if length < 0:
                _json_body(self, 400, {"error": "content_length_required"})
                return
            if length > MAX_BODY_BYTES:
                _json_body(self, 413, {"error": "payload_too_large"})
                return

            raw = self.rfile.read(length)
            if len(raw) != length:
                _json_body(self, 400, {"error": "incomplete_payload"})
                return
            if path == "/remote-event":
                if not receiver.remote_secret:
                    _json_body(self, 503, {"error": "remote_intake_unconfigured"})
                    return
                try:
                    verify_payload(
                        raw,
                        self.headers.get("X-Homelab-Event-Signature", ""),
                        self.headers.get("X-Homelab-Event-Timestamp", ""),
                        receiver.remote_secret,
                    )
                except AuthError:
                    _json_body(self, 401, {"error": "remote_event_unauthorized"})
                    return
                try:
                    claimed = receiver.claim_remote_request(
                        raw,
                        self.headers.get("X-Homelab-Event-Timestamp", ""),
                        self.headers.get("X-Homelab-Event-Signature", ""),
                    )
                except ControlPlaneError:
                    _json_body(self, 503, {"error": "remote_replay_unavailable"})
                    return
                if not claimed:
                    _json_body(self, 409, {"error": "remote_event_replay"})
                    return
            try:
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("payload must be an object")
                if path in {"/event", "/remote-event"}:
                    canonical = receiver.ingest_canonical(payload)
                    persisted = canonical.ingest
                    result = {
                        "events": 1,
                        "accepted": 0 if canonical.spooled else 1,
                        "spooled": int(canonical.spooled),
                        "duplicates": int(
                            persisted.duplicate if persisted is not None else False
                        ),
                        "action": canonical.decision.action,
                        "reason": canonical.decision.reason,
                        "page_intent_created": bool(
                            persisted.page_intent_created
                            if persisted is not None
                            else False
                        ),
                    }
                elif path == "/uptime-kuma":
                    result = receiver.ingest_uptime_kuma(payload)
                else:
                    result = receiver.ingest(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                _json_body(self, 400, {"error": "invalid_alertmanager_payload"})
                return
            except Exception:
                # Do not expose database paths, exception text, or payload
                # contents. Alertmanager can retry while the spool remains.
                _json_body(self, 503, {"error": "shadow_receiver_unavailable"})
                return
            _json_body(self, 200, result if isinstance(result, dict) else asdict(result))

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.allow_reuse_address = True
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("SHADOW_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SHADOW_PORT", "8088")))
    parser.add_argument("--db", default=os.environ.get("SHADOW_DB_PATH", "/var/lib/homelab-events/events.db"))
    parser.add_argument("--spool", default=os.environ.get("SHADOW_SPOOL_DIR", "/var/lib/homelab-events/spool"))
    args = parser.parse_args(argv)

    policy_path = os.environ.get("SHADOW_POLICY_PATH")
    policy = load_policy(policy_path) if policy_path else None
    remote_secret = os.environ.get("SHADOW_REMOTE_SECRET")
    remote_secret_file = os.environ.get("SHADOW_REMOTE_SECRET_FILE")
    if remote_secret is None and remote_secret_file:
        try:
            remote_secret = Path(remote_secret_file).read_text(encoding="utf-8").strip()
        except OSError:
            remote_secret = None
    receiver = ShadowReceiver(
        db_path=args.db,
        spool_dir=args.spool,
        policy=policy,
        remote_secret=remote_secret,
    )
    server = create_server(receiver, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.shutdown()
        server.server_close()
        receiver.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
