# Backup and restore

How backups are layered, how to verify them, what is and is not in them, and how to recover. For when each job runs, see the [daily schedule](daily-schedule.md). The design history is in [0006](../decisions/0006-backup-verification.md) and [0020](../decisions/0020-backup-pipeline-incidents.md).

## Operating rules

- Treat SQLite and service databases as the source of truth. Never edit a live database file by hand.
- Never copy a live SQLite file and call it a consistent backup. Use the service's own dump or SQLite's online `.backup`.
- Deploy one bounded component at a time and verify the live service straight afterward.
- Keep the layered Compose files together through `COMPOSE_FILE`. Do not run one fragment with `-f` when other files are referenced.
- Before a destructive change, record the current state and make a rollback copy of the file or directory.
- Volume removal is a separate destructive action and is never part of a rollback.

## Backup layers

| Layer | What it covers | Where it lands |
|---|---|---|
| **Server archive** | Application config and state. Databases are excluded by design | `backup` container, to Atlas |
| **Database dumps** | PostgreSQL dumps for Immich, Nextcloud and kan.bn. Newest three kept | Root cron, to Atlas |
| **SQLite snapshots** | Consistent online snapshots of Uptime Kuma, Grafana and Jellyfin, taken just before the archive | Timers, picked up by the archive |
| **Password vault** | A consistent SQLite snapshot, plus an encrypted copy to a removable drive when it is mounted | Root cron |
| **Event ledger** | A standalone SQLite snapshot of the alert ledger | Timer, picked up by the archive |
| **Game server** | The world and server data, backed up separately | `minecraft-backup` container |
| **Parity** | SnapRAID parity for Atlas and Aegir. Not a backup, see [storage](storage-pool-and-parity.md) | Argus |

The nightly Atlas to Aegir mirror was retired on 2026-10-31 when SnapRAID replaced it. Its lessons, especially about reporting sync success separately from verification, are in [0020](../decisions/0020-backup-pipeline-incidents.md).

Every artifact gets a `.sha256` manifest when it is written. The archive is a full tar triggered by a changed fingerprint, not an incremental, and it is skipped when nothing changed.

## What is in the archive

The archive has four source roots, read-only inside the container: application data, extra app configuration, one self-built app's source, and the Compose environment file. The policy for the application data:

- **Kept:** configuration and state for every application, including the proxy's database and certificates, DNS settings, monitoring history, and the reverse proxy and media applications' databases.
- **Restored from snapshots, not the live copy:** Uptime Kuma, Grafana, Jellyfin and the event ledger. The live SQLite files are still included for compatibility, but restores use the integrity-checked snapshots.
- **Excluded because reconstructible:** the Prometheus time-series data and the log store's history. Configuration and rules for both are tracked in the repository instead.
- **Excluded because backed up separately:** the game server world.
- **Mixed trees are kept whole.** A tree that holds both cache and durable settings is not split. One monitoring database turned out to hold API keys and notification rules alongside telemetry, so a proposed exclusion for it was rejected.

Two sensitive facts about the archive are tracked as known issues: it includes the Compose environment file, and the tar is unencrypted. Retaining secrets in an unencrypted archive versus introducing an encrypted recovery location is an open decision.

## Verification order

The container archive is the primary application backup, so check it first.

```bash
docker logs backup --tail 20
ls -lat /mnt/atlas/p2_backups/server/
ls -lat /mnt/atlas/p2_backups/databases/
```

A healthy run ends with `Backup complete.` and no gzip or tar error. Do not call backups healthy from the database script alone. Check the archive's size and contents, that the fingerprint is fresh, and the checksum manifests.

To verify the event ledger inside an archive without touching the live database:

```bash
PYTHONPATH=scripts/homelab-events python3 -m homelab_events.tools.verify_event_archive \
  --archive /mnt/atlas/p2_backups/server/backup-<timestamp>.tar.gz \
  --restore /tmp/events-archive-restore-<timestamp>.db
```

It must report `ok: true`, the snapshot member name, `integrity: ok`, and the expected event, incident and outbox counts. Keep the restored copy until the archive is accepted. A missing snapshot, a schema mismatch or a failed integrity check rejects the archive, and an unverified member is never copied over the live ledger.

## Recovery and rollback

A change or recovery is finished only when the service is active, logs show no new errors, the application's behavior works, backups are still verifiable, and nothing unrelated changed.

### Failed deployment

1. Stop changing files and capture status and logs.
2. Decide whether the cause is source, configuration, a dependency or a stale live artifact.
3. Restore the newest known-good timestamped copy or repository revision, restart only the affected service, and recheck the endpoint.
4. Keep the failed artifact until the incident is understood.

### Container or configuration rollback

1. Record `docker compose ps`, recent logs and the current image identifiers.
2. Restore the previous configuration and validate with `docker compose config --quiet`.
3. Recreate only the affected service, then check logs and dependent endpoints.

### Database restore

1. Stop writes to the service.
2. Pick a verified dump or snapshot by date and checksum, never a partial one.
3. Preserve the current database directory before replacing anything.
4. Restore with the service's documented command, run integrity checks, and do a read-only application check.
5. Restart and verify dependent jobs, dashboards and alerts.
6. Keep the pre-restore copy until the service has passed end-to-end checks.

### A verifier names a missing artifact

If the checksum verifier flags an artifact whose source is also gone, do not recreate it from another backup or copy a different dump under the old name, since that falsifies the timestamp and contents. Record the exact paths, confirm the producing job's failure in its log, and with authorization move only the exact empty manifest into a dated quarantine directory on every copy. Rerun the full verifier and update the metric only after it returns clean. Leave the original log unchanged as evidence. The worked example is [0020](../decisions/0020-backup-pipeline-incidents.md).

## Observability backup boundary

The log store, Vector's buffer and configuration, Grafana provisioning and data, Prometheus data and the event ledger snapshot are covered by the normal application archive. The protected observability credential file is a host-local, mode 0600 input and is restored by hand, never into Git or a runbook. See [observability recovery](observability-recovery.md).

## Still untested

- A full restore drill into a fresh location, from the archive alone, has not been done.
- A physical unplug or reboot test of the storage pool has not been done.
