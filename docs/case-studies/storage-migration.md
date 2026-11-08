# Case study: from a mirror to a pool with parity

In late October 2026 Legion's bulk storage changed from a nightly mirror to a second machine into a pooled pair of disks with a dedicated parity disk. This page covers why, the order the work was done in, what went wrong, and what was accepted. The resulting design is documented in [storage and parity](../runbooks/storage-pool-and-parity.md).

## Starting point

- **Atlas**, a 1 TB laptop HDD, held photos, backups and files. About 610 GiB was used.
- **Aegir**, a second 1 TB disk, sat on Citadel and received a nightly rsync mirror of Atlas over the network, with a dated deletion directory for history.
- The SSD was nearly full (93% used), partly because the TV and anime libraries lived on it.

The mirror worked, but it had real weaknesses. It depended on a laptop that reaches the network over Wi-Fi, and from 28 to 30 October its destination guard correctly refused to run because Citadel was unreachable. A mirror that protects data only when the network cooperates is a weak design. It also did nothing to check data at rest for silent corruption, and it could not add capacity.

## Decision

A third 1 TB drive was available, and the choice was between mdadm RAID 5 and SnapRAID with independent disks.

| | mdadm RAID 5 | SnapRAID + mergerfs |
|---|---|---|
| Each data disk readable on its own | No, one striped filesystem | Yes, ordinary ext4 |
| Deletions | Update parity immediately | Reviewed before the next sync |
| Failure beyond parity | Whole namespace at risk | Surviving disks stay readable |
| Growth | Restripe | Add a disk |
| Good for | Frequently changing data | Large, mostly static files |

The workload is mostly completed media, photos and archives, so the second column won. The pool uses mergerfs for a single namespace. Live databases, active SQLite files and container state stay on the SSD, because parity that is only current as of the last sync cannot protect them. The disks were named Atlas and Aegir for the pool's data, Argus for parity, and Arcadia for the pool.

## Sequence

The order was chosen so that nothing destructive happened before the evidence for it existed.

| Step | What happened |
|---|---|
| 1. Baseline | Recorded SMART, identity and capacity for all three disks. All three had zero reallocated and pending sectors. Aegir was SMR with over 260,000 start and stop cycles. |
| 2. Test the new disk | Argus's extended SMART test aborted three times, with 60%, 90% and 90% remaining. See below. |
| 3. Account for Aegir | Compared Aegir against Atlas before wiping it. Its mirror history held about 34 GB of older server and database backups with no personal media. |
| 4. Format Argus | Done first because it was empty. Zero reserved blocks and a large-file inode profile, mounted by UUID. |
| 5. Reformat Aegir | Atlas was unmounted and set read-only first, and the five containers using it were stopped. The format command was passed only Aegir's partition after rechecking model, serial, and absence of mounts. |
| 6. Create the pool | mergerfs over Atlas and Aegir, with guards that stop startup if either disk is missing or wrong. |
| 7. Move media | TV and anime moved to the pool, 146 files and 152.2 GB, verified by SHA-256 before the originals were deleted. The SSD recovered 141.8 GiB. |
| 8. Enable parity | Guarded sync and scrub, then the first full verification on 1 November. |

## What went wrong, and what it taught

**Disk identity was not trustworthy.** Both USB enclosures reported the same generic model and serial, and the `/dev/disk/by-id` aliases collided. One alias for a whole disk pointed at one drive while its partition alias pointed at another. Device names such as `sdc` also changed between reconnects, and an inspection mount ended up referencing a device that no longer existed. Every destructive step therefore rechecks the ATA serial, exact capacity and filesystem UUID immediately before acting, and persistent mounts use filesystem UUIDs. This is the single rule that made the formatting steps safe.

**The new disk would not finish a self-test.** Three extended tests ended as "aborted by host". There were no USB resets or suspend events in the logs, and runtime suspend time was zero, so USB autosuspend was not the cause even though the setting looked suspicious. The leading hypothesis was the enclosure's own idle management interrupting the test, which a smartmontools maintainer has documented for other bridges. A controlled trial held the USB power setting on and issued an uncached 4 KiB read every 30 seconds, and the test completed. That supports the hypothesis without proving it, because two conditions changed at once. The investigation also found the device's uncorrectable-error statistic rise from 10 to 12 with no explanation, so the disk was recorded as passing but not as healthy beyond doubt. This is why Argus's CRC count is tolerated but any rise during a run is a failure.

**The first full scrub reported 17,083 block errors.** They were in backup archives that had been rotated away during the scrub, with no I/O errors. The correct response was to withhold the completion marker, then fix the overlap and not the symptom. Backups and parity now share one maintenance lock.

**A legitimate deletion tripped the safety gate.** Removing ten files, 8.5 GB, exceeded 5% of one disk's tracked files. The gate was left as it was. A one-off tool took an approval naming the exact paths, sizes and content hash, and refused anything else.

## Risks accepted on purpose

Some scope was cut at my request, and each cut is a real trade-off.

- **The full checksum comparison of Aegir against Atlas was cancelled** in favor of matching relative paths and sizes. It would have taken many hours on 5400 RPM disks behind USB bridges. The 43 extra paths on Aegir were older backups, and the risk was judged acceptable for that kind of data.
- **No separate backup destination was provided** before reformatting Aegir, and its dated deletion history was discarded. Parity does not replace that history, so deleted-file recovery now depends on the backup layers and not on the pool.
- **Extended SMART tests on Atlas and Aegir were cancelled.** Only the new disk had to pass one.
- **No physical unplug or reboot test** was run after the cutover. On-access recovery was tested by stopping mounts and reading through the path, which is weaker.

These and the remaining gaps, such as the missing off-site copy and the unexercised production disk recovery, are listed in [known issues](../known-issues.md).

## Result

- One namespace over two disks, with parity on a third and a fail-closed deletion gate.
- 141.8 GiB freed on the SSD, with applications seeing the same paths through symlinks and matching binds.
- The nightly cross-machine mirror, and its dependency on Wi-Fi, retired.
- Failures reach the same event ledger and digest as the rest of the lab ([event pipeline](../runbooks/event-pipeline.md)).

## What I would do differently

Format the new disk and run its recovery drill earlier, since the fixture recovery came late. Buy enclosures that report unique serials, because the identity workarounds touched almost every step. And write the full-checksum waiver down as a decision record when it was made, and not only in a status note.
