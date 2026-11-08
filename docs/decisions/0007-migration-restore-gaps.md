# 0007. Migration restore: verify the claim, not an adjacent check

Date: 2026-07-23 · Status: accepted

## Context

During the July rebuild, the primary role moved between two machines. While restoring, two items that the migration plan had marked as backed up turned out to have no copy anywhere. This record exists so they are not quietly assumed to be handled.

## What was missing

**Photo and file app configuration (about 800 MB).** An earlier planning discussion stated that the config for the photo manager and the file server had been backed up. That was false for those two apps, while two other apps in the same list really were saved. A later "full cross-check" should have caught it but only compared the git repository against its copy, and these directories were never inside the repository, so that check could not have found the gap. It was confirmed absent across every backup set and the old workstation's full drive.

It was recovered without a backup:

- The photo manager regenerated its config on first boot, because its real state lives in Postgres and the photo library, both intact.
- For the file server, a minimal `config.php` was built against the restored database. The instance ID was recovered from the app-data folder name already on disk, so the app-data paths stayed valid. Server-side encryption was confirmed never to have been enabled, so file contents were never at risk. `occ files:scan` recognized 258 folders and 4,178 files.

**A tunnelling service's enrollment identity.** The plan had flagged this as needing backup, but no copy existed. Nothing to reconcile against, so it was accepted as lost, and re-enabling the service creates a new identity. Not a blocker for anything else.

**Three small systemd units** were also missing. Two of them reacted to lid-close events, which are now impossible because lid handling is disabled and all sleep targets are masked, so they were not restored.

## Lesson

A "done" claim made during planning is not safe to trust later, even after a cross-check reports success. The check must verify the same set of things the claim covers, not a narrower and more convenient set that also happens to pass.

## Consequences

- Live-file-only configuration that is not in git is a restore risk. This pushed the move of Citadel's services into Compose files that are tracked in git, described in [0009](0009-docker-on-citadel.md).
- Restore checks compare against the original claim, and not against a convenient proxy for it.
