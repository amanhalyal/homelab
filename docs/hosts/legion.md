# Legion

The primary server. It runs almost everything and is always on. This page is the quick reference. The cross-host picture is in the [architecture overview](../architecture.md) and the service list is in the [service catalog](../services/README.md).

## Identity

| | |
|---|---|
| Hardware | Lenovo Legion laptop, Intel i5-9300H (4c/8t), GTX 1650 with 4 GB VRAM, about 22 GB RAM, headless |
| OS | Ubuntu Server |
| Clock | IST (`Asia/Kolkata`), see [0008](../decisions/0008-cron-timezone.md) |
| Access | SSH, key only ([0005](../decisions/0005-ssh-key-only-authentication.md)) |
| Network | Reached over Tailscale. Legion's tailnet address is what the service domain resolves to |

## Layout

- **Repository:** `~/homelab`.
- **Compose project:** `nidavellir`, entered through `compose/.env` and assembled from category files: `base`, `network`, `apps`, `media`, `storage`, `monitoring`, `ops` and `backups`.
- **Service state:** `~/homelab/data/<service>`, unless a service page documents an exception. Runtime data, environment files, databases and credentials are never committed.
- **Bulk media** sits outside that tree by design, on the storage pool.
- **Independent projects** live under `~/projects`.

## Operating rules

- Validate before deploying: `cd ~/homelab/compose && docker compose config -q`.
- Use the category files as one project. Do not bring back one-file-per-service deployments without a new decision.
- Check systemd units, mounts, endpoints and backup state against the live host before changing a path. Docs can lag.
- Prometheus and Alertmanager detect, group and route. The event ledger and notification runbooks define persistence and delivery. See [alerting](../runbooks/alerting-and-notifications.md).
- The local LLM worker is on demand and is inactive when not needed.

## Storage

The SSD holds the OS, application state and movies. Bulk storage is two independent ext4 disks, Atlas and Aegir, pooled with mergerfs as **Arcadia**, plus a separate parity disk, **Argus**, for SnapRAID. Mounts are guarded by UUID checks. All of it is described in [storage pool and parity](../runbooks/storage-pool-and-parity.md), and the backup layers are in [backup and restore](../runbooks/backup-and-restore.md).

## Tuning in effect

Each of these has a decision record with the measurements behind it.

| Area | Setting | Record |
|---|---|---|
| CPU voltage | -125 mV core, -120 mV cache, validated in steps | [0001](../decisions/0001-cpu-undervolting-and-thermals.md) |
| Thermal limit | Throttle at 90°C through the hardware TCC offset | [0013](../decisions/0013-tcc-thermal-offset.md) |
| Frequency control | TLP is the single owner. `thermald` is disabled | [0001](../decisions/0001-cpu-undervolting-and-thermals.md) |
| Swap | zram at 25% of RAM, zstd, swappiness 15 | [0003](../decisions/0003-zram-and-swappiness.md) |
| Atlas power | APM 192, 4096 KB read-ahead | [0002](../decisions/0002-hdd-power-and-readahead.md) |
| Cooling | Fan cleaned. After cleaning, a 5-minute full load reached 59°C | [0001](../decisions/0001-cpu-undervolting-and-thermals.md) |

## Related

- [Daily schedule](../runbooks/daily-schedule.md): what runs when
- [Citadel](citadel.md) and [Nexus](nexus.md)
- [Runbooks](../runbooks/README.md) and [decision records](../decisions/README.md)
