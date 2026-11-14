# Host configuration

System-level configuration that lives outside Docker, kept per machine. Citadel's equivalent files sit with its application in [`../citadel/`](../citadel/README.md).

## Legion

| Path | Install to | Purpose |
|---|---|---|
| `legion/storage.fstab` | Merge into `/etc/fstab` | Mounts for Aegir and Argus by filesystem UUID, with on-access mounting and a 10 second device timeout, and the Arcadia mergerfs pool over Atlas and Aegir |
| `legion/udev/99-arcadia-storage.rules` | `/etc/udev/rules.d/` | Asks systemd to mount each disk by UUID when it is attached, and restores the pool when a backing disk reappears |
| `legion/snapraid/snapraid.conf` | `/etc/snapraid.conf` | Direct ext4 members only, with Argus as dedicated parity, three content copies, a 55°C temperature limit and the exclusions |
| `legion/systemd/` | `/etc/systemd/system/` | Mount guards, the guarded SnapRAID jobs and their timers, the monitor, the storage observer and Scrutiny refresh, and the media lifecycle |

The pool design, schedule and recovery steps are in [storage pool and parity](../docs/runbooks/storage-pool-and-parity.md), and the history of building it is the [storage migration case study](../docs/case-studies/storage-migration.md).

### Placeholders

- `<aegir-uuid>`, `<argus-uuid>` and `<atlas-uuid>` stand for filesystem UUIDs. Get the real ones from `blkid` and fill them into both the fstab entries and the udev rules.
- The units assume the checkout is at `/opt/homelab`.
- The units call helper programs under `/usr/local/libexec/snapraid/` and `/usr/local/libexec/arcadia-verify-mounts`. Their source is published with the scripts in a later step.

### Things worth noticing

- **Mounts are guarded, not trusted.** The pool mount is bound to both backing mounts and refuses to start unless a pre-start check confirms the expected UUIDs, so a missing disk can never look like an empty one.
- **One job template covers every SnapRAID run.** `snapraid-job@.service` takes the job name as its instance, and every instance requires and is bound to all three mounts. The timers only pick the instance and the time.
- **Interrupts are graceful.** Jobs use `KillMode=mixed` with `SIGINT`, so SnapRAID is asked to stop cleanly and can resume.
- **The completion tool pauses the backup containers.** `snapraid-job@finalize-initial.service` pauses them for its run and unpauses afterward, so an archive cannot change under a verification.
- **Media applications follow the pool.** `arcadia-media.service` is bound to the pool mount, so the applications stop before the pool is torn down and start again when it returns.

## Nexus

| Path | Purpose |
|---|---|
| `nexus/compose/netdata.yml` | The Netdata agent, scraped from Legion |
| `nexus/scripts/nexus-gpu-exporter.py` | A small Prometheus exporter that reads `nvidia-smi` and reports utilization, memory and temperature |
| `nexus/systemd/nexus-gpu-exporter.service` | Runs the exporter |

Nexus has no Prometheus of its own ([0018](../docs/decisions/0018-nexus-monitoring-and-sensors.md)). The exporter listens on all interfaces, so restrict it by firewall or bind it to the tailnet address if the network is not trusted.

## Not published

Samba shares, units for small personal services, and the one-time steps used to retire the old mirror.
