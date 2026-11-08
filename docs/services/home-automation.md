# Home automation

Home Assistant runs on Legion and ties together room lighting, the wallboard, remote power control for Nexus, task capture and a storage view. This page covers how it is deployed and the problems that shaped it. The wallboard integration is described on its [own page](wallboard.md).

## Deployment

- Home Assistant runs from the `stable` image in the main Compose project, on the shared service network, with its config directory under `data/homeassistant`.
- It sits behind the reverse proxy with WebSockets and TLS, and a published port provides direct access if the proxy path breaks.
- Watchtower updates are disabled for it, so updates are manual: back up first, read the release notes, record the current image digest, then pull and recreate.
- Time zone is IST, shutdown grace is 60 seconds, and the restart policy is `unless-stopped`.

### Trusted-proxy gotcha

The proxy was rejected with HTTP 400 until forwarded headers were trusted. Home Assistant trusts only the proxy's exact address, never a broad range. Two things made this fragile:

- The proxy's address changed when its container was recreated. It is now pinned in the Compose file.
- After a release migrated the HTTP settings into `.storage/http`, edits to the YAML were silently ignored. The pinned address and the stored trust entry must be kept in sync, using direct access to change the stored one.

## Lighting

**A WiZ smart bulb behind a Wi-Fi extender.** The extender presents every client to the router under one shared MAC address, so router reservations by MAC never held, and the bulb's address changed four times in one day. WiZ bulbs also have no static-IP option. Home Assistant's own rediscovery cannot help either, since it runs in a Docker bridge network and its discovery broadcasts never reach the LAN.

The fix is a small watcher on Legion, run by a systemd timer every minute:

1. Probe the saved address.
2. If that fails, search the local subnet for the bulb's MAC. A switched-off bulb is left alone.
3. Confirm the new address 10 seconds later.
4. Submit it through Home Assistant's own WiZ add-by-IP flow, which updates and reloads the existing entry with no restart and no file editing.

It has a `--dry-run` mode and uses a long-lived token held in a root-only file, never in the repo.

**An ESP32 driving an LED strip and a lamp relay,** through a small custom integration over the trusted-LAN HTTP API. Each part is polled every five seconds and read back immediately after a command. A failure marks only the affected light unavailable and polling recovers by itself. The device also accepts commands over a serial link from a PC, so the last accepted write wins, and Home Assistant never periodically reapplies a color. The device's address was moved after a firmware reflash, for the same shared-MAC reason as above.

## Dashboard

The default dashboard is app-editable, built from native Sections and Tile cards, single-column on phones. It provides room lighting, compact wallboard controls, Nexus wake, sleep, restart and cancel, quick task capture, and a habits shortcut.

- **Quick capture** adds a task to the real task board through the wallboard integration. The field clears only after a successful save and only if it still holds the submitted draft, so failures keep it and a newer draft typed during a pending save is never erased.
- Live UI edits belong to the storage-mode dashboard, so the tracked YAML is only the initial seed and is not redeployed over UI edits.
- Nexus power actions share a roughly one-minute grace period, and a cancel action covers both restart and shutdown. Shut down is disabled by default.

## Storage controls

A storage view shows capacities, drive temperatures and health, parity state, last sync and last full verification for the pool. It also exposes the deletion gate from [storage pool and parity](../runbooks/storage-pool-and-parity.md): an administrator can review pending deletions and approve an exact, expiring, single-use batch before the next sync, or choose **Hold sync**, which blocks both scheduled and approved syncs until it is explicitly released. The backend runs on Legion and uses the same maintenance lock and drive guards as the scheduled jobs.

## Source and ownership

Custom components and the package and dashboard seeds are tracked under `scripts/homeassistant/`. The live configuration and `.storage` are never overwritten by a deployment. Components are copied in, validated with Home Assistant's own `check_config` script inside its container, and then only the affected service is restarted.

## Backup

The server archive picks up the parent `data/` tree, but a verified Home Assistant restore and a consistent backup of its recorder database are still pending. Preserve the hidden `.storage` directory and all auth state. Until the recorder database joins the SQLite snapshot process, take a stopped-service copy before relying on a live filesystem archive.
