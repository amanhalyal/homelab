# homelab-events

The event ledger and notification pipeline behind the ntfy topics. Producers write canonical events, a policy decides what may page, and a bounded worker delivers. The design and operating notes are in the [event pipeline runbook](../../docs/runbooks/event-pipeline.md), and what the phone receives is in [alerting and notifications](../../docs/runbooks/alerting-and-notifications.md).

## Flow

```text
producers (Alertmanager, Uptime Kuma, cron and systemd jobs, Citadel)
   -> shadow_receiver.py / producer_cli.py   canonical intake, redaction, spool on failure
   -> ledger.py + schema.py                  SQLite: events, incidents, transitions, outbox
   -> notify_gate.py                         deterministic policy: critical, digest or log
   -> outbox.py / outbox_worker.py           leased, bounded, one intent at a time -> ntfy
```

## Files

| Group | Files |
|---|---|
| Storage | `schema.py`, `ledger.py`, `outbox.py`, `metrics.py`, `backup.py`, `snapshot_live_db.py`, `verify_event_archive.py` |
| Intake | `shadow_receiver.py`, `canonical_intake.py`, `shadow_intake.py`, `alertmanager_adapter.py`, `uptime_kuma_adapter.py`, `remote_auth.py` |
| Producers | `producer_cli.py`, `producer_emit.py`, `producer_event.sh`, `internal_event.sh` |
| Spool and replay | `spool.py`, `local_spool_replay.py`, `remote_spool_replay.py` |
| Policy | `notify_gate.py`, `policy.yaml` (shadow baseline), `policy-canary.yaml` (production canary) |
| Delivery | `outbox_worker.py`, `digest_delivery.py`, `digest_query.py` |
| Enrichment | `cerberus_enrichment.py`, `cerberus_drain.py` |
| Units | `*.service` and `*.timer`, and `ntfy-outbox-worker.service.d/production.conf` |

## Production guard

`policy.yaml` is the checked-in baseline and is deliberately not production-enabled. The outbox worker unit loads it and `HOMELAB_EVENTS_ENABLE_DELIVERY=0`, so it can run on a schedule and send nothing. Real delivery comes only from the drop-in, which switches to the separately installed, root-owned `policy-canary.yaml` and sets the delivery flag. Nothing here can page by editing a file in the repo.

## Placeholders

- The receiver's allowed remote hosts are listed in `producer_emit.py`. `100.64.0.1` stands for Legion's Tailscale address and `events.example.duckdns.org` for the TLS name in front of the receiver, so edit them for a deployment.
- Units run as `homelab` from `/opt/homelab`. `cerberus-drain.*` are **user** units, because they start and stop the model through the user service manager.
- The remote spool replay unit runs on Citadel, where its files are installed under `/usr/local/lib/homelab-events`.

## Cerberus enrichment

`cerberus-drain.timer` runs every 30 minutes and drains queued digest events through the local model, starting it on demand and stopping it afterward. See [event pipeline](../../docs/runbooks/event-pipeline.md#cerberus-enrichment) and [0016](../../docs/decisions/0016-alert-triage-classifier.md).

Test suites are not published.
