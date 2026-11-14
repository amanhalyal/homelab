# 0006. Verify backups with checksums, not "the sync ran"

Date: 2026-07-22, extended 2026-08-24 · Status: accepted

The nightly Atlas to Aegir mirror described here was retired on 2026-10-31 when SnapRAID parity replaced it. The verification design and its lessons still apply to the backup artifacts themselves.

## Context

The mirror's own health report only proved the rsync job ran without error. It said nothing about whether the copied data was intact or usable in a recovery. A backup tool's "no backups found" warning was also a false negative, since it only looked for its own archive format.

A first spot-check computed a combined SHA-256 over a sampled directory (two files, 1.5 GB) on both the source and the mirror, and the hashes matched. That was real proof for one directory, but it was not a restore drill.

A later audit found three more gaps:

- Integrity was checked only once, at write time (`gzip -t` after `tar`).
- Nothing re-checked a backup after it crossed the network to the machine holding the last-resort copy.
- The database dumps were never checked at all, even at creation.

## Decision

- Every backup layer writes a `.sha256` manifest next to the artifact when it is created, through a shared `write_manifest()` helper. This covers server, game-server and the database artifacts.
- After a successful mirror sync, the mirror script connects to Citadel and runs a verifier there. Each artifact gets **two independent checks**:
  1. **Checksum:** the bytes match the manifest, independent of rsync's transfer check.
  2. **Structure:** `tar -tzf` for tarballs and `gzip -t` for dumps. A matching checksum cannot catch a backup that was already broken when written, because `gzip -t` at write time validates only the gzip CRC and not the tar format inside.
- Any failure, or the verifier itself failing, sends an urgent notification and sets a metric that fires a critical Prometheus rule, using the same double layer as the mirror-failure alert.
- No manifests yet is explicitly not a failure.

## Evidence

All paths were tested live against real artifacts and the real remote host, not synthetically.

| Case | Test | Result |
|---|---|---|
| Healthy | Run on real backups | `OK: 7 manifest(s) verified` |
| Checksum mismatch | Corrupted a manifest hash | `MISMATCH` reported, alert fired within one evaluation cycle (about 30 s), cleared after restore |
| Structural only | Truncated a real dump and regenerated its manifest to match | Checksum passed, `gzip -t` still failed it, reported distinctly as `(structural)` |

The first version also had a bug that live testing caught: manifests recorded the container's absolute path, so they could never verify from a different mount on the other host. The helper now changes into the artifact's directory first and records a bare relative filename.

## Consequences

- The verifier is a plain script run over SSH, not part of any image. A change to it must be pulled on the remote machine too, and the first deployment failed exactly that way.
- Listing a 5 GB tarball adds real time to the post-sync step. That is fine nightly but not something to run more often without reconsidering cost.
- Prometheus rule changes made with an editor's atomic-rename write are not picked up by `SIGHUP`, because the container keeps the old file. A full container restart is the reliable way to reload.
- A restore drill, recovering into a fresh location, is still worth doing and remains the honest gap.
- Sync success and checksum success are separate signals. See [0020](0020-backup-pipeline-incidents.md).
