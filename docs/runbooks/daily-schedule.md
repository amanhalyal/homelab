# Daily schedule

When backups, syncs and recurring jobs run. Verified against live cron, systemd timers, container configuration and logs on 2026-11-03. All times are **IST (Asia/Kolkata)** unless stated. Legion's system clock is IST (see [0008](../decisions/0008-cron-timezone.md)). **Containers without a `TZ` setting run in UTC**, which explains the Watchtower row.

## Legion: once a day

| Time | Job | Defined in | Notes |
|---|---|---|---|
| 00:00 | OS housekeeping (`dpkg-db-backup`, `sysstat-rotate`) | systemd timers | |
| 00:20 | Vault copy to removable drive | root cron | Encrypted. Silent if the drive is not mounted |
| about 00:45 | `logrotate` | systemd timer | |
| 02:00 | Immich database backup | Immich built-in | 14 kept. Does not take the shared maintenance lock |
| 03:15 | SnapRAID sync | systemd timer | Up to 5 min random delay, deletion-gated. Refuses to start between 01:50 and 02:15 |
| 06:15 | SnapRAID partial scrub | systemd timer | 8% of eligible blocks older than 10 days |
| 09:00 | Daily digest | user cron | Reports server, game server, database and vault layers |
| 09:30 | Watchtower | `compose/ops.yml`, `0 0 4 * * *` | Runs at 04:00 UTC, which is 09:30 IST, because it has no `TZ`. Updates and recreates containers |
| 15:23 | Vault snapshot | root cron | Local SQLite snapshot |
| 23:20 | SQLite snapshots | systemd timer | Kuma, Grafana, Jellyfin. Inputs to the 23:30 archive |
| 23:25 | Event ledger snapshot | systemd timer | |
| 23:30 | Server backup | `backup` container, loop at 18:00 UTC | Three kept. Skips when unchanged |
| 23:30 | Game server backup | `minecraft-backup` container, same loop | Three kept. Skips when unchanged |
| 23:40 | Database backup | root cron, `scripts/db-backup/db-backup.sh` | Postgres dumps for Immich, Nextcloud and kan.bn |

The 23:20 to 23:40 window is deliberate: snapshots first, then the server archive, then dumps. The 03:15 SnapRAID sync then covers the finished archives.

## Legion: frequent jobs

| Frequency | Job |
|---|---|
| Every minute | Arcadia observer, ntfy outbox worker, local spool replay |
| Every 2 minutes | Kuma exporter (must stay in root's crontab, see below) |
| Every 5 minutes | GPU watch, Citadel recovery check (root cron) |
| Every 30 minutes | Scrutiny sync for the storage view |
| Hourly | Disk watch, SnapRAID monitor (alerts if the last good sync is over 36 hours old) |

## Weekly and monthly

| When | Job |
|---|---|
| Sunday 03:10 | `e2scrub_all`, `xfs_scrub_all` |
| Sunday 06:00 | Local LLM classifier self-test |
| Monday about 01:20 | `fstrim` |
| 1st of month, 09:30 | SnapRAID full scrub |

## Citadel

Not re-verified live on 2026-11-03. From its host notes: a connection watch and the wallboard health check every 2 minutes, and a Wi-Fi bounce on Monday at 05:30.

## Always on, not scheduled

- The video transcoder (Tdarr) runs around the clock. All 168 weekly schedule slots are enabled, so there is no overnight window.
- The photo service (Immich) runs continuously.

## Things to know

- **Watchtower and the monthly full scrub both fire at 09:30 IST on the 1st.** Watchtower may recreate containers while the scrub reads disks. It is not known to be harmful, but it is not intentional either.
- **Single-file bind mounts go stale.** The backup containers mount their script as one file. Replacing the file on the host, through a git checkout or an editor save, gives it a new inode, and the container keeps the old copy until it is recreated:

  ```bash
  cd compose && docker compose up -d --force-recreate --no-deps backup
  ```

  This also runs a backup immediately. A stale copy caused the server backup failures on 1 and 2 November.
- **The Kuma exporter must run from root's crontab.** The Kuma database is mode 0600 root, and from a normal user's crontab it silently reads nothing. A paused Kuma monitor is exported as `probe_success 0`.
- **Cron does not honor `CRON_TZ`** on this system. Verify any timing question against a real syslog timestamp, not the crontab alone.
