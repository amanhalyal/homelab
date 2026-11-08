# Storage pool and parity

How Legion's bulk storage is laid out, how it starts safely, and how to operate and recover it. The design rationale is in the [architecture overview](../architecture.md#storage).

## Layout

| Role | Name | Mount | Notes |
|---|---|---|---|
| Data disk | Atlas | `/mnt/atlas` | Independent ext4 |
| Data disk | Aegir | `/mnt/aegir` | Independent ext4 |
| Pool | Arcadia | `/mnt/arcadia` | mergerfs over Atlas and Aegir |
| Parity | Argus | `/mnt/argus` | SnapRAID parity only, outside the pool |
| SSD | n/a | `/` | Application databases and state, movies |

TV and anime live at `/mnt/arcadia/media`. The original paths under the home directory are absolute symlinks, and the containers bind the pool at the same absolute path, so existing application paths resolve unchanged.

## The pool (mergerfs)

| Option | Value | Why |
|---|---|---|
| Create policy | `mfs` | New files go to the branch with the most free space. It is not path-preserving, so a directory that began on Atlas does not pin future files there |
| `minfreespace` | 20 GiB | Branches under 20 GiB free are skipped for new files. A placement buffer, not a reservation |
| `moveonenospc` | `mfs` | If a write runs out of space, move the whole file to another branch and retry. Files are never split |
| Caching | `cache.files=partial`, `dropcacheonclose=true` | The mmap-compatible setting from the installed version's manual |
| `allow_other` | on | Applications access the pool under ordinary underlying permissions |

Existing files stay on the disk they were on. mergerfs does not copy, stripe or rebalance. Live application databases stay on the SSD, outside the pool.

### Safe startup

The mount is guarded so a missing disk can never be mistaken for an empty directory.

- A root-owned pre-start check refuses to mount Arcadia unless Atlas and Aegir are distinct, writable ext4 mounts with the expected UUIDs.
- The pool mount is bound to both backing mounts. Stopping or losing one stops the pool.
- The check is a oneshot without `RemainAfterExit`, so it reruns on every activation.
- Arcadia mounts on access, and udev rules request the mount when either disk appears.
- Losing Argus does not stop the pool.
- A systemd unit starts and stops the media applications with the pool, so they are stopped before it is torn down. systemd reporting success means Compose finished, not that the applications are healthy, so check readiness afterward.

```bash
systemctl status mnt-arcadia.mount mnt-arcadia.automount
sudo /usr/local/libexec/arcadia-verify-mounts
findmnt -t fuse.mergerfs /mnt/arcadia
df -h /mnt/arcadia
```

If startup fails, check both ext4 mounts and their UUIDs first, then:

```bash
sudo systemctl reset-failed mnt-arcadia.mount
sudo systemctl start mnt-arcadia.automount mnt-arcadia.mount
```

An autofs placeholder is not proof that a data disk is mounted. Stop the media applications before deliberately stopping the pool.

### Validation done at creation

- Seven guard tests: correct filesystem, missing mount, autofs placeholder, wrong UUID, root-filesystem fallback, read-only filesystem, ambiguous mount records.
- Files written separately to each disk were visible through one pool directory, and a new file landed on the emptier disk.
- With Aegir's mount stopped and masked, Arcadia stopped and did not mount, and no mergerfs mount remained. Restoring Aegir brought the pool back.
- No physical unplug or reboot was performed. That remains untested.

## Disk mounting and safe removal

The USB-attached disks (Aegir and Argus) are mounted by filesystem UUID, on boot, on attachment and on access. The USB bridges report duplicated serials, so bridge aliases under `/dev/disk/by-id` must not be used to pick these filesystems.

- `x-systemd.automount` supplies on-access mounts.
- `x-systemd.device-bound` ties each mount to its block device, so the mount stops if the device disappears.
- `nofail` and a 10-second device timeout let the machine boot with either disk missing.
- udev rules request each disk's automount and mount units when its UUID appears, so attaching a disk mounts it without a login session. The blocking mount does not run inside udev.
- No idle-unmount timer is set. With a disk absent, access to its path waits for the timeout and fails, and does not silently write to the root filesystem.
- Pool and parity jobs still verify real ext4 UUIDs. An autofs mount or an existing directory is not proof.

After editing the configuration:

```bash
sudo systemctl daemon-reload
sudo udevadm control --reload-rules
sudo systemctl start mnt-aegir.automount mnt-argus.automount
sudo systemctl start mnt-aegir.mount mnt-argus.mount
```

For devices that are already active, reloading rules updates udev properties but does not necessarily trigger a newly added dependency, so start the units explicitly.

**Safe removal.** Automatic handling of a disappearing disk cannot make unplugging during writes safe. Stop the jobs and services using the disk, stop its automount and mount together (for example `sudo systemctl stop mnt-argus.automount mnt-argus.mount`), and confirm nothing is mounted at the path before unplugging. Reattaching requests both units again. A reconnected disk may get a different `/dev/sdX` name, which is harmless since the configuration uses UUIDs.

If a filesystem is reformatted, update its UUID in both the fstab entry and the udev rule.

A removable encrypted pendrive that holds a vault copy uses the same pattern with a vfat UUID rule. Without the udev rule it mounted only if inserted at boot, and after a re-plug it stayed unmounted while the copy job exited silently. The digest showed it as skipped for two weeks before anyone noticed. An eager mount matters, because a bare automount leaves `lsblk` showing no mountpoint until something touches the path.

Validation: on-access recovery was tested directly, by stopping each mount with its automount still active and then reading a file through the path, which remounted it with the expected UUID. No physical unplug or replug was performed.

## Parity (SnapRAID)

SnapRAID runs on the direct filesystems, never on `/mnt/arcadia`. Content metadata is kept on each data disk and on the SSD, and the parity file lives on Argus. Private `.snapraid` directories are excluded from the protected data.

Excluded: `lost+found`, SnapRAID metadata, rsync partial directories, `*.part`, `*.tmp`, and staging trees. SnapRAID does not freeze running applications, so changes made during a sync can need the next sync. A failed sync is never reported as protection.

### Schedule (IST)

| Work | When |
|---|---|
| Guarded parity sync | Daily 03:15 |
| Partial scrub | Daily 06:15, 8% of blocks older than 10 days |
| Full scrub | First of each month, 09:30 |
| Availability and freshness monitor | 10 minutes after boot, then hourly |

Timers add up to five minutes of random delay and catch up missed runs. One exclusive lock prevents overlapping maintenance, and backups use the same lock. Scheduled maintenance refuses to run until the initial sync and full scrub have completed. No automatic `fix` is configured.

The monitor does not depend on the mount units, so it can report a missing disk even when systemd refuses to start maintenance. It also reports a sync older than 36 hours and an initial build that stopped without finishing. Failures go to the event ledger and the digest.

### Safety checks before every run

- All three mounts are distinct, writable ext4 with the expected UUIDs, and the physical disk serials match.
- SMART is readable and passing, reallocated and pending sector attributes are zero, and temperature is below 55°C. Argus's historical CRC count of 145 is tolerated, but a rise during a run is reported as a failure.
- Mounts are rechecked every minute while SnapRAID runs.
- **Deletion gate.** The wrapper refuses a sync if a protected top-level tree disappeared, the diff is unreadable, a removed path cannot be matched to the previous inventory, or deletions reach 100 files, 10 GiB, or 5% of a disk's tracked files. Deleted sizes come from the saved inventory and not a fresh estimate. A blocked sync leaves the previous parity untouched. There is no automatic bypass.

### Operating

```bash
systemctl list-timers 'snapraid*'
sudo cat /var/lib/snapraid/last-sync.json
sudo cat /var/lib/snapraid/monitor.json
sudo systemctl start --no-block snapraid-job@sync.service
sudo systemctl start --no-block snapraid-job@scrub.service
sudo systemctl start snapraid-monitor.service
```

Each job logs its stage and a private log path. Logs can contain filenames, so keep them private. To interrupt the initial build, stop its unit, which forwards SIGINT, then restart the same unit to resume. It does not restart itself after a reboot.

Run `snapraid touch` under the maintenance lock if `status` warns about zero sub-second timestamps after a large import. Without sub-second timestamps SnapRAID can only identify a moved file by path, so a reorganized folder looks like deletions plus additions and can trip the deletion gate. On 2026-11-05 this touched 42,022 files, and `diff` before and after was identical.

### Failure and recovery

1. Stop the three maintenance timers and any active job, and avoid new writes.
2. **Do not sync** to try to repair a failed disk, because that would overwrite the parity needed to recover.
3. Preserve all content copies and logs.
4. Follow the SnapRAID manual: point the failed disk at a separate empty replacement filesystem and run `fix` filtered to that disk, holding the maintenance lock throughout.

A small fixture was synced, recovered into an empty directory with a matching SHA-256, and fully scrubbed during setup. That validates the recovery path. A production disk recovery has not been done.

**Parity is not a backup.** It covers the last synced state, has no long-term deletion history, and does not cover the SSD. Backups are a separate system; see [backup and restore](backup-and-restore.md).

## Lessons from commissioning

- **The first full scrub reported 17,083 block errors across 16 files.** They were rotated-away backup archives and two tracking files, with zero I/O and zero data errors. The completion marker was correctly withheld. The cause was backup rotation overlapping the long verification, so the retry paused the sleeping backup containers for the run window.
- **A later scrub passed but the marker was still withheld** because Argus reached the 55°C cutoff. The wrapper now distinguishes high temperature from failed SMART, and after clean data checks it waits up to 30 minutes for cooling. Missing SMART, sector errors and identity failures still stop immediately.
- **A legitimate deletion exceeded the gate.** Ten files, 8.5 GB, were removed deliberately, which was over 5% of one disk's tracked files. The gate was not loosened. A one-off completion tool took a per-run approval listing the exact paths and sizes plus the expected content hash, and rejected anything else. It is not a scheduled bypass.
- **The monitor first treated a running oneshot in the `activating` state as inactive.** The fault was fixed and regression-tested.
- **The storage test suite stands at 49 tests,** covering the mount guard, serial, SMART and temperature rejection, deletion limits, malformed diffs, and systemd oneshot handling.
