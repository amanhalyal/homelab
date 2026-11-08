# Minecraft server

A Paper server (itzg/minecraft-server) that sleeps when nobody is playing and wakes when someone connects. The reasoning behind the lifecycle is in [0012](../decisions/0012-minecraft-idle-and-render-distance.md) and the DNS fix in [0021](../decisions/0021-minecraft-dual-stack-dns.md).

## Overview

| | |
|---|---|
| Server | Paper, in `compose/apps.yml` |
| Backup | A separate `minecraft-backup` container, change-triggered, with its own verification |
| Access | A private tunnel with per-user grants. Nothing is port-forwarded |
| Admin | RCON is enabled for manual use only |

Paper was kept after an A/B test against Fabric on the same world, client, distances, CPU allocation and heap. A crowded villager area tied at roughly 60 to 80 client FPS, but a second area ran at more than twice the FPS on Paper, while server tick time was healthy on both, about 22 ms for Paper against 24 ms. Fabric's jars remain as a rollback set, and one chunk-optimizing mod was disabled for unloading chunks too aggressively.

## Settings in effect

- View and simulation distance 16. Chunk work grows with the square of the distance, so the earlier 32 was too costly.
- `sync-chunk-writes=false`, which had been blocking the main thread on saves.
- 6 of 8 CPU cores, a 12 GB heap and G1GC tuning flags.
- `network-compression-threshold` and `entity-broadcast-range-percentage` are untuned candidates for reducing bandwidth to remote players.

## Lifecycle with lazymc

A proxy container owns the public port. While the server sleeps it answers server-list pings itself with a custom message. On a real join it starts the server container through the Docker socket and holds the client while it boots. After 600 seconds idle it stops the server, which releases the heap back to the host.

### Topology

- A dedicated dual-stack bridge network, defined in `compose/base.yml`, with static addresses for the proxy and the server.
- The proxy publishes the port and mounts the data directory and Docker socket read-only. The server publishes nothing and has `restart: "no"`, since lifecycle is the proxy's job.
- The tunnel still points at the same local port, which is now the proxy's, so switching from autopause needed no change there.
- All behavior is configured through `lazymc.*` labels on the server service.

### How it stops, and one operational rule

lazymc stops the server with `SIGTERM` only, delivered through the image's `mc-server-runner`, which forwards a clean `stop` to Paper. The server saves and exits 0. This is not configurable, and upstream is explicit that RCON is not a supported stop path.

Join handling is `hold,kick`, with a hold timeout of 28 seconds, kept under Minecraft's 30-second client timeout. A client that connects during a cold boot is held up to that long and then gets a message instead of a bare timeout.

**Any recreate of the server container outside lazymc, such as `up -d`, an image pull or a label change, must be followed by `docker compose restart lazymc`.** Otherwise lazymc's internal state desyncs, as it did on 2026-08-23, with `Failed to stop server, no more suitable stopping method to use` followed by a forced kill. A restart brings it back into step. Its startup stops any running server container first, so the server will be asleep immediately afterward.

### DNS and IPv6

Tailscale runs in direct DNS mode because Pi-hole owns host port 53, so the host resolver lists both MagicDNS addresses, IPv4 and IPv6, and Docker exposes both to containers on user-defined networks. The original bridge was IPv4-only, so startup worked until Paper's installer chose the IPv6 listener and failed with `Network is unreachable`. The container exited before Java started, and lazymc kept showing its starting message because the server never became healthy. The fix, described in [0021](../decisions/0021-minecraft-dual-stack-dns.md), was IPv6 on the bridge and not a per-service DNS override.

## Access control

Access is gated by the private tunnel's per-user grants. The server does not authenticate players itself, so the tunnel is the real access control, and a whitelist would be cheap extra insurance independent of it.
