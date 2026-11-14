# homelab-events

The event ledger and notification pipeline behind the ntfy topics. Producers write canonical events, a policy decides what may page, and a bounded worker delivers. The design and operating notes are in the [event pipeline runbook](../../docs/runbooks/event-pipeline.md), and what the phone receives is in [alerting and notifications](../../docs/runbooks/alerting-and-notifications.md).

## Flow

```text
producers (Alertmanager, Uptime Kuma, cron and systemd jobs, Citadel)
   -> intake/shadow_receiver.py, producers/producer_cli.py   intake, redaction, spool on failure
   -> store/ledger.py + store/schema.py                       SQLite: events, incidents, transitions, outbox
   -> policy/notify_gate.py                                   deterministic policy: critical, digest or log
   -> store/outbox.py, delivery/outbox_worker.py              leased, bounded, one intent at a time -> ntfy
```

## Layout

```text
homelab_events/        the importable package
  common.py            shared helpers: canonical and compact JSON, SHA-256,
                       UTC timestamps, atomic file writes
  store/               schema, ledger, outbox, backup, metrics
  policy/              notify_gate: the critical, digest or log decision
  intake/              shadow_receiver, canonical_intake, shadow_intake,
                       alertmanager_adapter, uptime_kuma_adapter, remote_auth
  producers/           producer_cli, producer_emit, spool,
                       local_spool_replay, remote_spool_replay
  delivery/            outbox_worker, digest_delivery, digest_query
  enrichment/          cerberus_enrichment, cerberus_drain
  tools/               snapshot_live_db, verify_event_archive
lib/                   producer_event.sh and internal_event.sh, sourced by jobs
config/                policy.yaml (shadow baseline), policy-canary.yaml (production)
systemd/               system/ (Legion), citadel/, user/, common/sandbox.conf, install.sh
```

Run anything as a module with the directory on `PYTHONPATH`:

```sh
PYTHONPATH=scripts/homelab-events python3 -m homelab_events.tools.verify_event_archive --help
```

## Production guard

`config/policy.yaml` is the checked-in baseline and is deliberately not production-enabled. The outbox worker unit loads it with `HOMELAB_EVENTS_ENABLE_DELIVERY=0`, so it can run on a schedule and send nothing. Real delivery comes only from the drop-in `ntfy-outbox-worker.service.d/production-canary.conf`, which switches to the separately installed, root-owned `policy-canary.yaml` and sets the delivery flag. Nothing here can page by editing a file in the repo.

## Units

Every oneshot service links one shared sandbox file, `systemd/common/sandbox.conf`, as `<unit>.service.d/10-sandbox.conf`, so the hardening is written once. A unit keeps only what is specific to it: `ProtectHome`, the allowed address families and the writable paths. The timers share too little to be worth a common file.

`systemd/install.sh system|citadel|user [--dry-run]` copies the units, resolves those links, backs up anything it replaces and reloads systemd. It never enables, starts or restarts a unit.

## Placeholders

- The receiver's allowed remote hosts are listed in `homelab_events/producers/producer_emit.py`. `100.64.0.1` stands for Legion's Tailscale address and `events.example.duckdns.org` for the TLS name in front of the receiver, so edit them for a deployment.
- Units run as `homelab` from `/opt/homelab`. `systemd/user/cerberus-drain.*` are **user** units, because they start and stop the model through the user service manager.
- `systemd/citadel/` is installed on Citadel with the package and `lib/` under `/usr/local/lib/homelab-events`.

## Cerberus enrichment

`cerberus-drain.timer` runs every 30 minutes and drains queued digest events through the local model, starting it on demand and stopping it afterward. See [event pipeline](../../docs/runbooks/event-pipeline.md#cerberus-enrichment) and [0016](../../docs/decisions/0016-alert-triage-classifier.md).

Test suites are not published.
