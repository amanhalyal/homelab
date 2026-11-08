# 0004. Reverse proxy: harden everywhere, reject redundancy

Date: 2026-07-22 · Status: accepted

## Context

Nginx Proxy Manager is the single entry point for all web services, so it is a single point of failure. Two questions came up: is its built-in protection actually on, and should it have a standby?

## Exploit blocking: enabled on all hosts

All 19 proxy hosts had `block_exploits` set to false, which was checked through NPM's API rather than assumed. It was enabled on all 19 through the API and verified with `nginx -t` on the regenerated config, plus a spot-check of two host configs for the `block-exploits.conf` include. This was a real gap that had existed from the start, closed at no ongoing cost.

## Redundancy: designed in depth, then rejected

The design was a periodic rsync of NPM's data directory (database and certificates) to a dormant standby on another machine, plus a failover script to start it and repoint the dynamic DNS record.

Automatic failover was proposed and then withdrawn after working through fail-back. The existing dynamic-DNS container restarts on boot and re-asserts the primary's address every five minutes, whether or not the reverse proxy behind it is healthy. Recovery of DNS is therefore already automatic and blind to health. Adding automatic failover on top of that would create a second system that can misfire, to protect against a rare failure (the whole primary host dying) on a home network.

**Decision: drop the whole effort, not only the automation.** The mirror directories and any cron or systemd artifacts were removed. NPM stays a single point of failure as an accepted risk and not an oversight.

## Consequences

- Anything that can change reachability of the whole homelab at once stays manual, with a human in the loop. This rule shaped later decisions too.
- If redundancy is revisited, start from the fail-back problem described here instead of rediscovering it.
- The NPM admin login is not stored in the repo and is supplied when needed.
