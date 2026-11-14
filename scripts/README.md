# Scripts

Automation that runs on Legion. Everything here records its result in the event ledger and never sends a notification directly, so grouping, deduplication and policy apply to every producer ([event pipeline](../docs/runbooks/event-pipeline.md)).

| Directory | Purpose | When it runs |
|---|---|---|
| [`storage/`](storage/) | Guarded SnapRAID jobs, the deletion gate, mount and identity checks, the storage observer, the Scrutiny refresh and the control API for Home Assistant | systemd timers ([`hosts/legion/systemd`](../hosts/legion/systemd/)) |
| [`server-backup/`](server-backup/) | The nightly archive of configuration and state, with a manifest, required-member and size-collapse checks | The `backup` container, 23:30 IST |
| [`db-backup/`](db-backup/) | Postgres dumps for Immich, Nextcloud and kan.bn, each with a manifest | Root cron, 23:40 IST |
| [`sqlite-snapshot/`](sqlite-snapshot/) | Integrity-checked online snapshots of the Kuma, Grafana and Jellyfin databases | systemd timer, 23:20 IST |
| [`disk-watch/`](disk-watch/) | Records an event when the SSD, Atlas, Aegir or Arcadia passes 90% full, or a tmpfs passes 70% | Hourly |
| [`gpu-watch/`](gpu-watch/) | Records an event on GPU temperature or VRAM pressure | Every 5 minutes |
| [`kuma-exporter/`](kuma-exporter/) | Pushes Uptime Kuma monitor status to Pushgateway for the wallboard | Every 2 minutes, from root's crontab |

The full schedule, with the reasons for the ordering, is in the [daily schedule](../docs/runbooks/daily-schedule.md).

## Placeholders and paths

- Disk identity is in two places: `DISKS` in `storage/snapraid_job.py` and `BRANCHES` in `storage/arcadia_verify_mounts.py`. Replace `<atlas-uuid>`, `<aegir-uuid>`, `<argus-uuid>` and the three serials with real values. The scripts refuse to run until the UUID and the physical serial of every disk match.
- The checkout is assumed to be at `/opt/homelab`.
- Programs under `storage/` are installed to `/usr/local/libexec/snapraid/`, which is where the units in `hosts/legion/systemd/` expect them. The one-off completion tool reads its approval file from `SNAPRAID_APPROVAL_FILE`.

## Design notes

- **Fail closed.** The backup scripts refuse to run on a missing source, an unmounted destination, a stale snapshot, or a selection that shrank by more than 60%, and a failed `tar` or `gzip -t` leaves the previous good backup and fingerprint in place.
- **One lock.** Every job that publishes archives or touches parity holds the same maintenance lock, so a backup never overlaps a sync or scrub.
- **Manifests carry a bare filename,** so a checksum verifies from any mount point.
- **Watchdogs are silent when healthy** and record a single event when something crosses a threshold.

Test suites are not published.
