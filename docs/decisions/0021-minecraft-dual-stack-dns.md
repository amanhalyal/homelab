# 0021. Give the game-server network IPv6 instead of pinning DNS

Date: 2026-09-14 · Status: accepted

## Context

After a host reboot, the proxy that wakes the Minecraft server could start the container, but Paper exited during bootstrap. The installer failed to resolve `fill.papermc.io`. Its last DNS attempt went to Tailscale's IPv6 MagicDNS listener and failed with `Network is unreachable`.

The host advertises both IPv4 and IPv6 MagicDNS listeners. The dedicated Docker bridge only had an IPv4 subnet, so the IPv6 listener appeared in resolver discovery but could not be reached. Earlier starts had worked only because resolution happened to finish over IPv4, so they were not proof that the topology was right.

Setting `dns: 100.100.100.100` on the service restored it immediately. It would also have hidden the inconsistent topology and made this one service a special case.

## Decision

Keep host and container DNS discovery as they are, and make the game-server bridge dual-stack in the Compose file:

- IPv4 subnet `172.19.0.0/16`
- IPv6 subnet `fd00:172:19::/64`
- `enable_ipv6: true`

The temporary DNS override was removed. Pi-hole and Tailscale DNS settings did not change, and the systemd-resolved stub stays disabled because Pi-hole owns port 53 on the host.

## Evidence

- `docker compose config -q` passed.
- An isolated dual-stack bridge reached the IPv6 listener directly.
- The exact Paper installer downloaded its jar on that bridge with no DNS override.
- The deployed container had no DNS override, received both an IPv4 and an IPv6 address, and became healthy.
- A join-style probe through the proxy woke the sleeping server, which logged `Done (8.471s)`.

## Consequences

Both MagicDNS listeners are now reachable from inside Docker, and the service no longer carries a resolver-specific exception. Changing the bridge's addressing recreates both Minecraft containers, so it must be scheduled while no players are online.

The general lesson is to fix the network so it matches what discovery advertises, rather than overriding one service's resolver to hide the mismatch.
