# 0022. Page when Citadel is unreachable, and surface open incidents

Date: 2026-11-01 · Status: accepted

## Context

Citadel was down for about five days after its battery ran out. Nothing paged for the outage itself:

- The only detector, a stale health-check alert, was warning severity and digest-only.
- The battery alert paged correctly at 18%, then resolved itself about 35 minutes later, because its metric disappeared along with the host.
- No rule watched the Citadel scrape target.
- The morning digest never mentioned it. Its block of routed alerts read a file that an earlier version of the pipeline wrote and the current one no longer does, so it was always empty. The incident was open in the ledger for the whole outage but never reached the digest text.

## Decision

1. **A critical rule, `CitadelUnreachable`.** It fires when the Citadel scrape target is down and no heartbeat has arrived for 10 minutes, held for 15 minutes. Requiring both makes it a reliable "the host or network is gone" signal. A Netdata-only restart does not trip it, because the two signals travel different paths, a Tailscale scrape and an SSH push. Backtested over the previous 10 days, it was true for exactly one run, the real outage, and would have paged about 15 minutes in. Nexus is intentionally on demand and not covered.
2. **Map it into the event pipeline** and add it to the critical allowlist in the checked-in policy, which is what the receiver loads. The deployed canary policy in the root-owned directory needed the same allowlist entry, applied separately.
3. **An OPEN INCIDENTS section in the daily digest,** built read-only from the ledger, oldest first with start time and age, capped at 10 lines.

## What the new section exposed

The first preview listed six long-open incidents. Checked against reality, two were real outages and four were stale ledger state.

- **Tdarr and Immich's machine-learning container never started** after being recreated by Watchtower. An unattended package upgrade had moved the NVIDIA userspace libraries while the loaded kernel module stayed old, so every create with the NVIDIA runtime failed with a driver and library mismatch. A reboot cleared the first window, and the cause of the second was not established. `unless-stopped` does not retry a container that failed to be created. The container-down rule was widened to also match the `created` and `dead` states, and a backtest showed it would have caught all of these.
- **Four stale incidents were closed** with matching `resolved` events. The cause for the backup pair was a design gap: a success event has a different dedupe key, so it can never close a failure incident. The backup script now emits a matching resolved event after a recovery.
- **A stale reverse-proxy target** and a dashboard tile still used an old address for a service that had moved. A scan of every host confirmed the generated configs now match the database.
- **An exporter had been silently dead for about 51 days.** It read a database file readable only by root, but ran from a normal user's crontab, so it failed to open the file and pushed nothing, and the wallboard's Kuma panels went stale. It now runs from root's crontab.
- **Uptime Kuma monitors were repaired** by editing its database directly with the container stopped, since that version has no REST API. Paused monitors that pointed at an old address were retargeted, three were renamed, one retired container's monitor was removed, and new monitors were added for three services. All 46 monitors are now up.

## Consequences

- A host going silent needs its own alert. Metrics from a dead host vanish, which makes related alerts resolve and look healthy.
- An alert that only exists in the ledger helps nobody until something reads it. The digest now does.
- Silent failures are the real hazard. Several problems here had lasted days or weeks because they failed without noise.
- The digest also surfaced existing state and not new problems, which is the point of showing it.
