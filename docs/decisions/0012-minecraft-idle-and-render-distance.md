# 0012. Game server: revert render distance, replace autopause with a wake-on-join proxy

Date: 2026-08-01 to 2026-08-22 · Status: accepted

## Context

When the Minecraft server moved to Legion, its view and simulation distance were raised from 16 to 32, the vanilla maximum. The reasoning was that Legion has a faster CPU and NVMe storage. Memory was left unchanged. (The GPU contributes nothing, since the server runs headless and chunk loading is a server-side CPU and RAM concern.)

Three weeks later chunk loading felt slow. The first instinct was to add RAM, raising the heap from 8 GB to 12 GB, and it did not help. `docker stats` showed the container at 261% CPU against a 4-core cap. Chunk work grows with the square of the distance, roughly 4,225 chunks per player at 32 versus 441 at the default of 10, and that work is CPU-bound. The original assumption failed under real load.

A separate finding: the container still held 8 to 9 GB after long idle periods. `ENABLE_AUTOPAUSE` sends `SIGSTOP` to the Java process, which stops CPU use but cannot release memory, because a frozen process cannot run garbage collection or uncommit its heap. The memory was held in a frozen snapshot, trading RAM for instant resume.

## Decision

**Performance:**
- View and simulation distance back to 16.
- `sync-chunk-writes` off, because it blocked the main thread on saves.
- CPU cap from 4 to 6 of 8 cores, since host load was only about 3. Two cores stay as slack, because the transcoder can spike concurrently.
- G1GC tuning flags, to cut garbage-collection stutter.
- Heap left at 12 GB by choice. On this 22 GB host, pre-touching it at boot has once crashed the JIT compiler under memory pressure, and the player simply reconnects.

**Idle lifecycle.** Replace autopause with `lazymc-docker-proxy`:
- The proxy owns the public port and answers server-list pings itself while the server sleeps, with a custom "sleeping" message.
- On a real join it starts the server container through the Docker socket, holds the client for up to 25 seconds, then hands over transparently.
- It sleeps after 600 seconds idle, longer than autopause's 300, since a stop and start is heavier than a pause and resume.
- The server sits on a dedicated Docker network and no longer publishes the port directly.

**Rejected:** the image's `ENABLE_AUTOSTOP`, which has no wake mechanism, so someone must start the container by hand. The proxy keeps "just connect and it works".

## Consequences

- The first join after idle is a true cold boot, not an instant resume. That is the accepted price for returning memory to the host.
- The proxy holds a read-only Docker socket mount, which still grants full API control. This is not a new posture, since other management tools in the stack already have it.
- Verified with `docker compose config -q`, healthy containers, a server-list ping that returned the sleeping message, and real cold-boot joins. The 25 second hold has been enough for the world's load time.
- The proxy's network setup later needed a dual-stack fix. See [0021](0021-minecraft-dual-stack-dns.md).
