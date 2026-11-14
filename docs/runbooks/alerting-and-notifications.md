# Alerting and notifications

What the homelab may send to a phone, when it may interrupt, what is deliberately digest-only, and how to check or roll back the delivery path. The mechanics of the ledger behind it are in the [event pipeline](event-pipeline.md). The design reasoning is in the [architecture overview](../architecture.md#monitoring-and-alerting) and [0016](../decisions/0016-alert-triage-classifier.md).

## Summary

Two kinds of delivery reach ntfy:

1. **Rare, immediate pages** on `homelab-critical` for deterministic, action-worthy failures.
2. **One health digest** at 09:00 IST on `homelab-digest`.

Warnings, routine successes, ordinary service-down events, normal recoveries and self-healing are recorded in the ledger and summarized in the digest, not sent as individual notifications.

```text
producer or monitoring system
    -> canonical structured intake
    -> redaction + SQLite event and incident ledger
    -> deterministic notification policy
    -> transactional notification outbox
    -> bounded ntfy worker
    -> ntfy topic
```

ntfy is the last hop. It is not the event bus, the incident store, an Alertmanager replacement or the source of truth. An older topic from before the cutover is kept only as rollback history, and **no producer may post directly to any ntfy topic**.

## Topology

- **Metric alerts.** Prometheus to Alertmanager to the receiver's `/alertmanager` endpoint. Alertmanager keeps authority over evaluation, grouping, inhibition and silencing. Grouping is by `alertname`, with `group_wait` 30s, `group_interval` 5m, `repeat_interval` 12h and `send_resolved` on. A resolved callback enters the ledger and does not become a standalone page.
- **Uptime Kuma.** A webhook to the receiver's `/uptime-kuma` endpoint. It is not a direct ntfy channel.
- **Legion producers.** Cron jobs, systemd units and containers use a shared helper and canonical CLI, covering backup, disk, GPU, connectivity, recovery and checksum paths. A producer's work is independent of notification delivery, so a failed notification never turns a finished backup into a failed one.
- **Citadel producers.** The same helper, sending HMAC-authenticated HTTPS events through the reverse proxy to the receiver. If intake fails, the event is spooled locally and replayed later.
- **Delivery worker.** A systemd oneshot run by a one-minute timer, attempting **one intent per run** with a 10-second HTTP timeout. A separate one-minute timer replays the local spool into SQLite, and never posts to ntfy.

The repository's unit ships closed by default, with delivery disabled and the shadow policy. Production delivery is enabled by a separately installed drop-in and a root-owned production policy. Always inspect the effective unit rather than inferring behavior from the source file.

## Topics

| Topic | Purpose | Behavior |
|---|---|---|
| `homelab-critical` | Immediate, action-worthy incidents | Only deterministic critical decisions. One page per incident cycle, 12-hour repeat interval |
| `homelab-digest` | Scheduled and digest-routed information | Daily at 09:00 IST, with retry of a failed window |

A producer cannot choose a critical page just by labeling an event `critical`. The policy and its evidence decide. The worker keeps a JSON envelope for idempotency but sends only the message text, with the title as a header and critical events at maximum priority.

## What can page immediately

**An explicit allowlist** of event names, accepted only when the policy accepts the event:

- SMART overall failure, offline uncorrectable sectors, reallocated-sector emergency, NVMe critical warning
- Backup checksum failure and loss of the last known-good backup
- A mirror drop guard and battery shutdown risk
- `citadel_unreachable`: the scrape target is down and no heartbeat has arrived for 10 minutes, held for 15 ([0022](../decisions/0022-citadel-unreachable-alert.md))

**Backups escalate** from digest to page only on deterministic evidence: the known-good copy is missing, older than 26 hours, or three consecutive runs failed. An ordinary failure with a fresh good copy is not a page.

**Core services** (Alertmanager, Prometheus, ntfy) can page after at least three failed probes across a 15-minute window. A single failed probe does not.

**Rate limits.** At most one critical intent per incident cycle, reminders no more often than every 12 hours, stable delivery IDs, and bounded leases and retries. Delivery is **at-least-once**. A crash after ntfy accepts a request but before SQLite records `sent` can cause a retry.

## What stays out of the phone

Unless a separate deterministic critical condition is met, these are digest or log only: routine success, ordinary backup or mirror failure with a fresh copy, resolved events, recovery without a prior page, container restarts and flapping, Citadel self-healing, classifier or digest-worker degradation, transient battery draining, warning temperatures, and non-allowlisted events that merely claim `critical`. They stay in the ledger as history and are never silently converted into pages.

## Recovery semantics

A recovery is not inferred from a successful notification. It needs the matching resolved event from the producer, a recorded incident transition, and an independent healthy observation. A recovery page is sent only if the original critical page was actually delivered. Administrative changes, such as retiring a monitor, are recorded as administrative and are never described as health recovery.

## Daily digest

At 09:00 IST one deterministic window is queued and sent by the outbox worker. The cursor advances only after the transport acknowledges, so a failed send stays retryable. It reports:

- Open incidents first, oldest first with age ([0022](../decisions/0022-citadel-unreachable-alert.md))
- Resolved events, warning trends, backup and integrity activity, routine success totals
- Items needing human attention, and advisory triage notes from the local model for digest-routed events
- Backup layers, drive health, and recent transcoding jobs

A backup container's `No changes since last backup, skipping.` renders as skipped, not as a failure. An explicit error or a missing artifact is still a failure.

## Expected behavior

| Situation | Result |
|---|---|
| Atlas SMART overall failure | Immediate page |
| One backup failure while a fresh copy exists | No page, summarized in the digest |
| Three consecutive backup failures | Escalates to a page |
| Citadel unreachable for 15+ minutes | Immediate page, and listed under open incidents until resolved |
| Citadel briefly self-heals Wi-Fi | No page, history only |
| A monitor comes back with no prior page | No recovery page, ledger and digest only |
| The 09:00 digest send fails | The same window stays queued, no false success |
| The local classifier degrades | Log and digest only, never a page about itself |

## Data and redaction

The system of record is a local SQLite database that is never mounted over a network. Before persistence, hashing, spooling, logging or retry, credential-shaped fields and embedded Basic or Bearer values are redacted. Secrets come only from protected environment files. Raw ntfy response bodies are not stored, only allowlisted metadata, and spool files are atomic with mode 0600. When intake fails, an event is spooled and replayed. It is never bypassed with a direct ntfy post.

## Safe checks

These are read-only and publish nothing.

```sh
sudo systemctl is-active ntfy-outbox-worker.timer local-spool-replay.timer
sudo systemctl cat ntfy-outbox-worker.service     # effective command and drop-in
sudo journalctl -u ntfy-outbox-worker.service -n 20 --no-pager
```

Check receiver, Alertmanager and Prometheus readiness through their health endpoints, and check ledger health with a few counts: events, open incidents, pending and failed outbox rows, and `PRAGMA integrity_check`.

**Do not post a synthetic event to either active topic as a health check.** Use a temporary ledger with a fake transport, or an unsubscribed topic.

## Incident handling

**A critical page arrives.** Record the topic, title and time. Verify the real producer and host. Do not close the incident because ntfy delivered. Check the ledger's cycle and evidence. After recovery, require the matching healthy observation and the ledger transition before calling it resolved.

**The digest is missing.** Check, in order: the timer and the latest worker journal, pending or failed outbox rows, the digest's delivery ID and cursor, ntfy reachability and status, and whether the window is still queued. Never recreate a digest by posting to ntfy or advancing the cursor by hand.

**The receiver is down.** Producers should spool. Restore the receiver, let the replay timer drain, then check ledger counts and outbox state. Do not reintroduce direct posts as a quick fix.

## Rollback

The first action disables human delivery while keeping the ledger and evidence:

```sh
sudo systemctl disable --now ntfy-outbox-worker.timer
sudo rm /etc/systemd/system/ntfy-outbox-worker.service.d/production-canary.conf
sudo systemctl daemon-reload
```

Queued intents and the database are untouched. A full route rollback is a separate, approved action: preserve current config and database, restore the saved Alertmanager copy and run `amtool check-config`, restore Kuma only from a verified snapshot with Kuma stopped, then verify routing, active alerts, worker state and history. Never run `docker compose down -v` during a notification rollback.

## Changing notification behavior

1. Update the checked-in shadow policy and this document together.
2. Update the installed production policy only through the guarded procedure.
3. Save a rollback snapshot before changing Alertmanager, Kuma, the worker drop-in or the schema.
4. Run the full event test suite and the configuration checks.
5. Verify the effective live command, receiver health, ledger integrity and outbox backlog.
6. Never add a direct ntfy writer to bypass canonical intake, even temporarily.

## Acceptance at cutover

The cutover was verified with a software gate (120 tests, compilation, shell syntax, merged Compose validation, Prometheus rule validation and systemd unit checks) and a transport gate that used a random unsubscribed topic and a synthetic event, recording a successful HTTP 200 and a terminal `sent` row. Afterward, with approval, one clearly labeled low-priority message was posted to each active topic and matched in authenticated history. Those checks prove transport and persistence. They did not pass through the ledger and outbox, and device receipt was not independently verified. No natural critical delivery is claimed.
