# 0020. Backup pipeline incidents and hardening

Date: 2026-09-09 to 2026-09-14 · Status: accepted

The Atlas to Aegir mirror referenced here was retired on 2026-10-31 and replaced by SnapRAID parity. The database-backup fixes and the lessons still apply.

## Summary

A single night of alerts combined two separate problems, and a third incident followed a few days later.

1. **A backup-generation race.** A missing database artifact and an empty checksum manifest came from two cron jobs running at the same second. The mirror had not corrupted anything.
2. **A misleading digest.** The morning summary reported `0B transferred` although about 6.6 GB moved.
3. **Transient link failures.** A Wi-Fi interruption left a partial transfer.

The key lesson is that a sync reporting success and a checksum verifier reporting success are separate signals.

## Incident 1: the duplicate cron race

Both the non-root crontab and root's contained the database-backup job at 23:40. Both processes ran at the same second and generated the same timestamped filename. The non-root run hit root-owned runtime files while the root run was still using the shared intermediate dump. That file was gone when post-processing ran, giving `No such file or directory` from `gzip` and `sha256sum`. The compressed artifact never existed, but an empty `.sha256` file was left behind, and the Citadel-side verifier correctly reported a mismatch. An earlier valid artifact matched byte for byte on both disks, which ruled out transfer corruption.

**Fix:**

- Root cron is the only schedule. The duplicate entry was removed and non-root execution is rejected.
- Runs are serialized with `flock` on a lock file.
- The script fails closed on any dump, compression, checksum or cleanup error.
- Artifacts and manifests are published atomically, only after validation, and an empty manifest never accompanies a success.

## Incident 2: the digest read the first rsync block

The mirror runs separate rsync passes per top-level tree. The digest parser read only the first statistics block, which was an unchanged 6 KB pass, and reported `0B`. The real run transferred 41 files and about 6.6 GB.

**Fix:** aggregate every statistics block from the latest completed run, and report sync and checksum state independently, for example `SYNC OK / CHECKSUM FAILED`. The digest is a summary and never a substitute for the verifier.

## The orphan manifest

After the fix, one alert still fired because the historical empty manifest remained on both disks. The affected paths were checked independently on both hosts before any repair. Another dump could not be copied under that name, since that would falsify its contents and timestamp. Both empty manifests were instead **moved, not deleted,** into a mirrored quarantine directory outside the verifier's live paths. Afterwards the verifier returned `OK: 21 manifest(s) verified`, the metric returned to 1, and the alert cleared. Deleting the quarantine requires a separate review.

## Incident 3: interruption-safe retries

A Wi-Fi drop left a 3.66 GB partial of a 7.46 GB archive. The mirror stays one-way and segmented, but each pass now retries within the same locked run:

- Four attempts, with backoff of 30, 60 and 120 seconds.
- Retries happen only for transport failures: rsync exit codes 10, 12, 30 and 35, and SSH exit 255. Permission and destination I/O errors are not retried.
- `--partial` is kept so a retry resumes the interrupted file. A partial is a recovery asset, and deleting it would throw away the bytes already copied.
- Each attempt is wrapped in start and end markers, and the digest counts only successful attempts, so failed-attempt statistics are never added to the resumed total.
- After the budget is exhausted, the run still fails loudly.

## Investigation order for future reports

1. Check the local backup producer and its error files before blaming the mirror.
2. Confirm there is only one scheduler and inspect the lock.
3. Confirm the mirror log contains `Mirror sync complete.`
4. Aggregate every per-tree statistics block from that run.
5. Check the sync and checksum metrics independently.
6. Inspect the exact mismatch list before changing any object on the mirror.

Related: [0006](0006-backup-verification.md).
