# Event pipeline

The SQLite-first event ledger and notification outbox behind the ntfy topics. This page covers the mechanics: what is stored, the modules, how to exercise it safely, and how it fails. What the phone receives, and when, is in [alerting and notifications](alerting-and-notifications.md).

## Scope

The pipeline stores redacted, canonical event envelopes, incident state and transitions, notification intents, digest cursors, outbox state and bounded transport receipts. It is the durable history behind the human-facing topics. ntfy itself is only the last hop.

Prometheus and Alertmanager remain the authority for evaluation, grouping, inhibition, silencing and routing. This layer does not replace them. The outbox worker is the only active producer of ntfy notifications, and old direct writers and shims are kept only in rollback form.

The database lives on Legion's local filesystem. It is never mounted from another host over the network. Remote producers use authenticated intake and a local spool instead.

```text
Alertmanager / Uptime Kuma / Legion producers / Citadel producers
    -> canonical intake and recursive redaction
    -> Legion-local SQLite event and incident ledger
    -> deterministic policy decision
    -> transactional notification outbox
    -> one-intent bounded worker
    -> ntfy over HTTPS
```

The receiver container still carries the name `homelab-event-shadow` from the migration. It is the canonical live intake and not a shadow path.

## Modules

| Module | Role |
|---|---|
| `schema.py` | SQLite schema, WAL and busy-timeout setup, migrations, integrity check. Schema version 7 |
| `ledger.py` | Validated atomic ingestion, incident cycles and transitions, redaction, digest windows, retention |
| `notify_gate.py` | Deterministic critical, digest or log policy evaluation |
| `canonical_intake.py` | Policy-before-intent intake, with spool fallback |
| `outbox.py`, `outbox_worker.py` | Stable delivery IDs, leases, bounded retries, terminal states, leased ntfy delivery with production guards |
| `digest_query.py`, `digest_worker.py`, `digest_delivery.py` | Read-only digest sections, durable enqueue and cursor handling |
| `alertmanager_adapter.py`, `uptime_kuma_adapter.py` | Normalize Alertmanager v4 and Kuma payloads |
| `shadow_receiver.py`, `remote_auth.py` | Internal and HMAC-authenticated remote HTTP receiver |
| `spool.py`, `local_spool_replay.py`, `remote_spool_replay.py` | Local spool and replay after an intake failure |
| `producer_emit.py`, `producer_cli.py`, `producer_event.sh`, `eventctl.py` | Producer helpers and ingest CLIs |
| `remediation_worker.py`, `worker.py` | Observe-only incident worker |
| `cerberus_enrichment.py` | Optional digest-event enrichment |
| `backup.py`, `snapshot_live_db.py`, `verify_event_archive.py` | Online backup, restore and archive verification |
| `metrics.py` | Prometheus text rendering |
| `policy.yaml` | Checked-in **shadow** policy, never production-enabled |

The checked-in policy is deliberately the shadow baseline, with `production_enabled: false`. Production delivery uses a separately installed, root-owned policy through a guarded systemd drop-in. This split is a rollback and fail-closed control. Do not edit the checked-in policy to turn delivery on.

## Exercising it safely

Use a temporary database and never point a synthetic event at a phone-visible topic. `eventctl.py` validates the v1 envelope and writes only to SQLite:

```sh
python3 scripts/homelab-events/eventctl.py --db /tmp/test/events.db --event '{
  "schema_version": 1, "event_id": "evt-example-001",
  "dedupe_key": "service/legion/example", "source": "script",
  "host": "legion", "service": "example", "resource": "example",
  "event": "observation", "status": "failure", "severity": "warning",
  "page_policy": "digest", "observed_at": "2026-09-10T00:00:00Z",
  "message": "example event", "labels": {}, "annotations": {},
  "runbook": "observe-only", "evidence": []
}'
```

- A retry with the same `event_id` is idempotent.
- Passing `--policy` makes an untrusted `page_policy: critical` field unable to create a critical intent on its own.
- `--metrics` prints a read-only Prometheus snapshot.
- `shadowctl.py` ingests captured Alertmanager JSON, replays a spool, and builds a digest for a time range. None of its commands contact ntfy, and the digest command does not advance the cursor.
- `worker.py` records a `needs_human` proposal and executes no host command.
- The outbox worker without `--send` is a read-only snapshot. Sending needs a production-enabled policy, an explicit CLI override and `HOMELAB_EVENTS_ENABLE_DELIVERY=1`, and the checked-in shadow policy fails that guard on purpose.

## Backup, restore and retention

Use SQLite's online backup API. Do not copy `events.db` alongside its `-wal` and `-shm` files with `cp` or a tar while the database is live.

```sh
PYTHONPATH=scripts/homelab-events python3 -c '
from backup import backup_database
backup_database("data/homelab-events/events.db", "/safe/backup/homelab-events.db")'
```

Restore to a separate path first, run `integrity_check` and compare table counts, and only then consider replacing a runtime database in an approved maintenance window. The backup layers are described in [backup and restore](backup-and-restore.md).

Retention never infers expiry from age. First mark exact event IDs expired with a reason, then purge only their raw payloads. The event identity, normalized summary, incident row and transition history stay searchable. Purging preserves summaries and is not history deletion.

## Failure semantics

- **`intake_timeout`:** a write lock exceeded the timeout, and the event was not accepted.
- **`store_write_failed`:** SQLite rejected the write, for example read-only or full. The event was not accepted.
- `eventctl` returns exit code 75 for both, with a JSON error on stderr, and never prints success.
- **Outbox delivery is bounded at-least-once.** An expired lease can be reclaimed with the same delivery ID. `sent` and terminal `failed` rows are never silently recreated.
- The metrics snapshot exposes event count, open incidents, page intents and failures, stuck actions, the digest cursor, intake age, outbox backlog and write and timeout counters. The renderer exists, and an exporter is not deployed.

## Receiver and rollback checks

Validate without publishing anything:

```sh
docker exec homelab-event-shadow wget -qO- --timeout=3 http://127.0.0.1:8088/healthz
docker exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
docker exec alertmanager wget -qO- --timeout=3 http://127.0.0.1:9093/-/ready
```

A full route rollback is bounded: preserve the current config and database, restore the saved Alertmanager copy, run `amtool check-config`, recreate only Alertmanager, and re-enable the legacy shim only if the rollback requires it. Kuma has its own snapshot boundary. Stop it before any database restore and verify integrity before restarting. Never use `docker compose down -v`. The details are in [alerting and notifications](alerting-and-notifications.md#rollback).

## Cutover acceptance (2026-09-11)

Alertmanager, Kuma, Legion producers and Citadel producers all use canonical intake. Local and remote spooling and replay are deployed and bounded. Policy, redaction, deduplication, incident transitions, outbox leases and retries, digest cursors and rollback drills passed. A real HTTPS transport canary on a random unsubscribed topic recorded HTTP 200, one attempt and a terminal `sent` row. It was not phone-visible and is not evidence of a natural incident.

The point-in-time checkpoint was 76 events, 56 incidents, 76 transitions, 2 open incidents, 0 pending outbox rows, 2 sent, 0 failed, and integrity ok. Both open incidents were real, unrelated warnings that were left visible and not hidden to make the cutover look clean.

Raw-log centralization was deliberately a separate, later phase. See [observability recovery](observability-recovery.md).
