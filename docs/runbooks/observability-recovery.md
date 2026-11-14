# Observability recovery and safe remediation

Recovery procedures for the observability stack on Legion: VictoriaLogs, Vector, Grafana, Prometheus, Alertmanager, the optional Cerberus enrichment, and the event ledger. Everything here is operator-executed. No unattended shell execution is authorized.

## Safety boundaries

- **Critical delivery does not depend on the log plane.** Prometheus, Alertmanager and the canonical event and outbox path stay authoritative. Cerberus, Grafana, Vector and VictoriaLogs can all be down without affecting a critical page.
- Do not send synthetic events through Alertmanager or to a phone-visible topic. If an operator authorizes a one-off transport test, send one clearly labeled low-priority message straight to the chosen topic and verify it in authenticated server history. Synthetic log probes use `log_source=synthetic` and query only the authenticated VictoriaLogs endpoint.
- Never print secrets. The observability credential and its environment file are mode 0600, live outside Git, and are restored by hand. Keep a mode 0600 rollback copy when rotating.
- Remediation is manual. The ledger records incidents and notification intents only, and nothing executes a repair.

## Baseline

Run from the Compose directory so the repository `.env` layering applies:

```bash
docker compose config -q
docker ps --format '{{.Names}}\t{{.Status}}'
docker compose logs --tail 50 victorialogs vector grafana prometheus alertmanager
docker exec vector vector validate --config-dir /etc/vector
```

Validate the config directory, not the file and its directory together. Loading both loads the config twice and reports a false duplicate-component error.

VictoriaLogs is bound to the tailnet and needs the protected basic credential. Grafana reaches it through the `victoriametrics-logs-datasource` plugin on its internal URL. Queries use LogsQL, for example `{service="example"} | "word filter"`. Loki's `|~` syntax is not valid for this build.

## Failure paths

**ntfy outage.** The outbox worker must leave rows pending or failed and retry with bounded backoff. It must never declare an incident resolved. Check the ledger, the worker journal and the response metadata. Restore ntfy, run the guarded worker, and confirm each row becomes `sent` with no duplicate delivery ID. Never restore unrestricted direct producer posts.

**SQLite unavailable or locked.** Stop writes, check free space and permissions, and use the local spool and replay. Do not mount SQLite over a network or delete WAL files. Restore the latest verified standalone snapshot to a separate path first, run `PRAGMA integrity_check`, compare table counts, and only then restore the service in an approved window.

**Vector or VictoriaLogs outage.** Check container health, restart count, the internal `/health`, Vector metrics and the disk buffer. The buffer is bounded at 4 GiB with `when_full: block`, so a full buffer applies backpressure and does not silently drop. Restart only the affected service after preserving logs, query for a known event, and confirm the buffer drains.

**Grafana or proxy outage.** Check the container and provisioning logs, then the datasource and dashboard UIDs. Test VictoriaLogs directly with an authenticated LogsQL query before touching proxy routing. Do not hand-edit proxy state.

**Cerberus outage.** Cerberus is optional enrichment for digest and tier-2 events. A timeout or unavailable model fails open to the structured digest event and records `cerberus_unavailable`. It cannot delay, suppress or decide a critical page, and the classifier never pages about itself.

**Host or storage failure.** Capture `df -B1`, `docker ps`, failed units and the latest backup artifacts before changing anything. Do not reboot, prune backups or delete history as a first response. Restore application data from the verified archive and databases from their verified dumps. Restore the event ledger from its snapshot, never from a live WAL-bearing copy.

## Backup and restore

The normal archive includes the tracked observability configuration and runtime data: VictoriaLogs, Vector's buffer and config, Grafana provisioning and data, Prometheus data, and the ledger snapshot. The credential and environment file are host-local inputs that are never copied into Git or documentation. To restore them, recreate the files mode 0600, verify ownership, and recreate only the dependent containers. Verify the archive first, as described in [backup and restore](backup-and-restore.md#verification-order).

## Safe remediation model

Recovery is a manual loop that starts from an open incident. An observe-only remediation worker that recorded proposals was removed in November 2026 because it had never run, so there is no action table and no proposal step.

Any future executor must require an explicit `--execute` flag and use a checked-in allowlist, a cooldown, a timeout and a rollback path. Destructive commands, reboot, backup deletion, gateway changes and credential printing are permanently outside the allowlist.

Verification gates are independent of any action's exit status: endpoint health, Prometheus target state, Alertmanager's active alerts, ledger integrity, outbox state, and an independent healthy observation before an incident is closed.

### Diagnosis and repair loop

```text
alert -> ledger incident -> diagnosis -> operator approval
      -> bounded allowlisted repair -> independent health check
      -> resolved, or escalate again
```

List what is open without changing anything:

```bash
sqlite3 -readonly data/homelab-events/events.db \
  "select id, dedupe_key, severity, occurrence_count from incidents where state = 'open'"
```

Diagnosis can be done by a person or by an AI assistant run in a read-only mode. It reads this runbook and the live state, and returns root-cause evidence plus a bounded repair proposal. It must not edit files, restart services, send notifications or run destructive commands.

A repair needs an interactive session where the operator approves each specific allowlisted command, and reviews the scope and the before and after health evidence. A one-shot diagnostic run is never an unattended repair executor. If the post-check fails, the incident stays open and escalates again. Recovery is never marked from a command's result alone.

## Retention

VictoriaLogs keeps 30 days, and its disk ceiling was reduced from 50 GB to 20 GB because the root filesystem had about 104 GB free and also carries Prometheus, journald, Docker logs and recovery artifacts. The 10 GB free-space guard stays. Prometheus retention is unchanged, and no historical data was deleted.

## Evidence classes

Mark evidence as live operational, synthetic, authentic, aggregate-only or inferred. A successful synthetic log probe proves ingestion and query plumbing only. It is not evidence of an authentic Alertmanager callback.
