# Event pipeline

The SQLite-first event ledger and notification outbox behind the ntfy topics. This page covers the mechanics: what is stored, the modules, how to exercise it safely, and how it fails. What the phone receives, and when, is in [alerting and notifications](alerting-and-notifications.md).

## Scope

The pipeline stores redacted, canonical event envelopes, incident state and transitions, notification intents, digest cursors, outbox state and bounded transport receipts. It is the durable history behind the human-facing topics. ntfy itself is only the last hop.

Prometheus and Alertmanager remain the authority for evaluation, grouping, inhibition, silencing and routing. This layer does not replace them. The outbox worker is the only active producer of ntfy notifications, and the old direct writers and shims have been removed.

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
| `digest_query.py`, `digest_delivery.py` | Open-incident query for the daily digest, durable enqueue and cursor handling |
| `alertmanager_adapter.py`, `uptime_kuma_adapter.py` | Normalize Alertmanager v4 and Kuma payloads |
| `shadow_receiver.py`, `remote_auth.py` | Internal and HMAC-authenticated remote HTTP receiver |
| `spool.py`, `local_spool_replay.py`, `remote_spool_replay.py` | Local spool and replay after an intake failure |
| `producer_emit.py`, `producer_cli.py`, `producer_event.sh`, `internal_event.sh` | Producer helpers and the ingest CLI |
| `cerberus_enrichment.py`, `cerberus_drain.py` | Model briefs for digest-routed events, drained by a timer |
| `backup.py`, `snapshot_live_db.py`, `verify_event_archive.py` | Online backup, restore and archive verification |
| `metrics.py` | Prometheus text rendering |
| `policy.yaml`, `policy-canary.yaml` | The checked-in **shadow** policy, never production-enabled, and the production canary policy that is installed root-owned |

The checked-in policy is deliberately the shadow baseline, with `production_enabled: false`. Production delivery uses a separately installed, root-owned policy through a guarded systemd drop-in. This split is a rollback and fail-closed control. Do not edit the checked-in policy to turn delivery on.

## Exercising it safely

Use a temporary database and never point a synthetic event at a phone-visible topic. `producer_cli.py` validates the v1 envelope and, in `canonical` mode, writes it to SQLite:

```sh
python3 scripts/homelab-events/producer_cli.py \
  --mode canonical --db /tmp/test/events.db --spool /tmp/test/spool \
  --policy scripts/homelab-events/policy.yaml \
  --source script --host legion --service example --resource example \
  --event-name observation --status failure --severity warning \
  --page-policy digest --message "example event" \
  --dedupe-key service/legion/example
```

- A retry with the same `event_id` is idempotent.
- The policy is what lets a `page_policy: critical` field create a critical intent at all, so an untrusted field cannot page on its own.
- Captured Alertmanager JSON can be posted to a locally started `shadow_receiver.py` pointing at a temporary database. None of this contacts ntfy.
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
- The ingest CLI returns a non-success exit and a JSON error for both, and never prints success.
- **Outbox delivery is bounded at-least-once.** An expired lease can be reclaimed with the same delivery ID. `sent` and terminal `failed` rows are never silently recreated.
- The metrics snapshot exposes event count, open incidents, page intents and failures, the digest cursor, intake age, outbox backlog and write and timeout counters. The renderer exists, and an exporter is not deployed.

## Cerberus enrichment

Every digest-routed `firing`, `failure` or `resolved` event is queued in `cerberus_enrichments`. A user timer, `cerberus-drain.timer`, runs `cerberus_drain.py` every 30 minutes:

1. It expires queue entries older than 36 hours.
2. If nothing is eligible it exits without touching the GPU.
3. Otherwise it starts the model service on demand, waits for its health endpoint, enriches up to 20 events one call at a time, and stops the model again if this run started it.
4. If the model cannot start it backs off for an hour. After three consecutive bad answers it stops and leaves the rest pending.

The brief is three bounded fields, `what`, `since` and `check`. Enrichment never creates an outbox row and never runs before the policy decision, so it cannot delay, suppress or decide a page. The daily digest adds an advisory "Triage notes" section from the briefs of non-resolved events in its window, grouped per resource. A failed enrichment only means a missing note.

## Receiver and rollback checks

Validate without publishing anything:

```sh
docker exec homelab-event-shadow wget -qO- --timeout=3 http://127.0.0.1:8088/healthz
docker exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml
docker exec alertmanager wget -qO- --timeout=3 http://127.0.0.1:9093/-/ready
```

A full route rollback is bounded: preserve the current config and database, restore the saved Alertmanager copy, run `amtool check-config`, and recreate only Alertmanager. The legacy shim no longer exists, so a rollback restores the saved routing and nothing else. Kuma has its own snapshot boundary. Stop it before any database restore and verify integrity before restarting. Never use `docker compose down -v`. The details are in [alerting and notifications](alerting-and-notifications.md#rollback).

## Cutover acceptance (2026-09-11)

Alertmanager, Kuma, Legion producers and Citadel producers all use canonical intake. Local and remote spooling and replay are deployed and bounded. Policy, redaction, deduplication, incident transitions, outbox leases and retries, digest cursors and rollback drills passed. A real HTTPS transport canary on a random unsubscribed topic recorded HTTP 200, one attempt and a terminal `sent` row. It was not phone-visible and is not evidence of a natural incident.

The point-in-time checkpoint was 76 events, 56 incidents, 76 transitions, 2 open incidents, 0 pending outbox rows, 2 sent, 0 failed, and integrity ok. Both open incidents were real, unrelated warnings that were left visible and not hidden to make the cutover look clean.

Raw-log centralization was deliberately a separate, later phase. See [observability recovery](observability-recovery.md).
