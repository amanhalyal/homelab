# 0009. Run Citadel's services in Docker, keep display and OS-bound parts native

Date: 2026-07-29 · Status: accepted

## Context

After the July rebuild, Citadel was kept free of Docker on the theory that its small CPU needed conserving. That reasoning did not hold up. Before the rebuild, the same machine ran the entire 40-plus container stack, including photo ML inference and video transcoding. The heat it produced came from that workload, not from Docker's own overhead, and the cleaning in [0001](0001-cpu-undervolting-and-thermals.md) had already fixed the thermals.

The real reason to containerize was restorability. Native systemd units are not tracked in git, and [0007](0007-migration-restore-gaps.md) had just shown units being lost in a rebuild for exactly that reason. Compose files are tracked like the rest of the repo.

## Decision

Install Docker CE from the official apt repository and keep a single Compose file for Citadel, with no category layering because the service count is small.

**Containerized:** Netdata (with host networking) and the wallboard server and rotation, which are plain Python 3.12 with no dependencies beyond the standard library.

**Left native on purpose:**

| Component | Reason |
|---|---|
| Control bot | Sends Wake-on-LAN packets with raw network access and drives the kiosk browser through the display session. Containerizing it needs host networking, an X11 socket and `NET_RAW`, which is most of a full host grant for no isolation benefit. |
| Kiosk browser | Bound to the physical display. |
| Health-check script | A trivial one-shot. It now checks container state through `docker inspect`. |
| Samba | Tied to host users and print-driver serving. `smb.conf` is tracked in git instead. |

## Gotchas

- **Netdata needed host networking.** The bridge-network pattern copied from Legion does not work, since Citadel has no reverse proxy on a shared Docker network to reach it by name.
- **The wallboard server hardcodes absolute paths.** Mounting its folder at a different path inside the container caused `FileNotFoundError`. Mounting at the identical absolute path fixed it without editing the script.
- The old native units were disabled and not deleted, which left an inert rollback path.

## Evidence

Netdata was reachable and scraped by Prometheus with no config change, since host networking preserves the address. The wallboard served live traffic to the kiosk, and the health check reported healthy for all tracked services.

## Consequences

The wallboard server was later moved back to native after CPU contention. See the wallboard reliability record.
