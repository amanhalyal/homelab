# Known issues and accepted risks

Deliberate non-fixes, open items and a short record of problems that were found and closed. The aim is to record what is known to be imperfect, so it is not rediscovered or re-argued later. Where a decision record holds the full reasoning, it is linked.

## Accepted risks

These are understood and left as they are, for the reasons given.

### Reverse proxy is a single point of failure

Every web service goes through one Nginx Proxy Manager instance on Legion. A standby with a failover script was designed and then dropped after working through the fail-back cases, because an automatic failover on the gateway path was judged riskier than a short outage ([0004](decisions/0004-reverse-proxy-hardening-and-redundancy.md)). If it fails, everything behind it is unreachable until it is recovered by hand, and no failover runbook exists.

### Tailnet membership is the main access boundary

Nothing is port-forwarded, and the hostnames resolve to a Tailscale address ([architecture](architecture.md#network-and-trust)). A device on the tailnet can reach every service at the network level, and each service still has its own login. Two consequences are accepted:

- The local model endpoint on Nexus has no authentication of its own. It is reachable only from the tailnet, and the machine is usually off.
- Authentication strength on the admin panels (Grafana, Portainer, Pi-hole, Uptime Kuma) has not been audited. Password strength and two-factor use are an open item, listed below.

### The server archive is unencrypted and holds secrets

The nightly archive includes the Compose environment file and a few recovery keys, and the tar is not encrypted. They are kept because a restore without them would silently lose reachability. The alternatives are retaining secrets in an unencrypted archive or introducing an encrypted recovery location, and that decision is still open ([backup and restore](runbooks/backup-and-restore.md)).

### No off-site backup

Parity protects against a disk failure, and the backup layers protect against data and configuration loss on Legion. Neither survives the loss of Legion itself, such as theft, fire or electrical damage. The password vault gets an encrypted copy on a removable drive when it is mounted, and that is the only copy kept apart from the machine. An off-site plan using an external drive is intended, and cloud backup is deliberately not part of it.

### Restores have not been drilled

Backup integrity is verified with checksums and structure checks ([0006](decisions/0006-backup-verification.md)), and the parity recovery path was exercised on a small fixture. A full restore of the server archive into a fresh location, and a production disk recovery from parity, have not been done. The Home Assistant recorder database is also not yet part of the consistent snapshot process ([home automation](services/home-automation.md#backup)).

### The storage disks are modest hardware on USB

Atlas, Aegir and Argus are 2.5-inch laptop drives. Aegir and Argus connect through USB bridges that report duplicate serials, and Aegir is SMR. Argus has historical device-statistics errors that were not explained by its passing extended test, which needed periodic read I/O to complete. The mount guards, serial checks and SMART gates exist because of this ([storage and parity](runbooks/storage-pool-and-parity.md)). Replacing the enclosures or moving to direct SATA would remove the uncertainty, and has not been done.

### Nexus is usually off

Nothing is designed to assume it is reachable. Anything that involves it checks reachability first, and it cannot be relied on as Legion and Citadel can.

### The local model degrades over long sessions

Throughput on Nexus steps down from about 30 to about 5 tokens per second over roughly an hour of continuous use. Three independent upstream reports show the same pattern on unrelated hardware, and no root cause has been identified. A watchdog restarts the server when it is idle, and a rare restart can still fail one request ([Nexus](hosts/nexus.md)).

### Citadel's Wi-Fi uplink is the weakest link

Citadel reaches the network only through a 2.4 GHz range extender, and a cable to that room is not possible. In August this produced Wi-Fi bounces and two watchdog reboots in a day. A separate failure silently dropped TCP packets that carried OpenSSH's default DSCP marking, which broke SSH in both directions for about five hours while ping, UDP and Tailscale kept working. The workarounds are `IPQoS none` for the SSH host entry and routing Citadel's own SSH to Legion over the Tailscale address. If SSH to Citadel dies while ping and Tailscale still work, suspect this first. The real fix is placement or replacement of the extender ([Citadel](hosts/citadel.md)).

### A tunnel identity was lost in the rebuild

The tunnel used for the game server had no backup, so re-enabling it creates a new identity and the reserved name may not return cleanly ([0007](decisions/0007-migration-restore-gaps.md)).

## Open items

- **Admin panel authentication audit.** Review password strength and two-factor options on the admin panels.
- **Watchtower and the monthly scrub share a minute.** Watchtower has no timezone setting, so its 04:00 UTC run lands at 09:30 IST, the same minute as the full scrub on the first of the month. It is not known to be harmful, and moving one of them is the fix ([daily schedule](runbooks/daily-schedule.md)).
- **Encrypted recovery location.** Decide how the archive's secrets should be held, as above.

## Resolved

Each of these had a real cost, and the lesson is the useful part.

- **Stale single-file bind mount broke backups (November 2026).** The backup container mounts its script as one file. Replacing the file on the host gave it a new inode, and the container kept running the old copy, which still required a file that had been removed. Two nightly backups failed, and the incident stayed open in the digest for days because the recovery signal depended on a marker the old script never wrote. Recreating the container fixed it, and the incident was closed with a manual resolved event. Prevention: recreate containers after changing a bind-mounted script, or mount the script's directory instead. The same trap applies to the game-server backup container.
- **A memory limit corrupted backups (August 2026).** A 512 MB limit let the cgroup OOM-killer kill `gzip` in the middle of the archive. The script ignored tar's exit code, so four truncated archives were marked complete, and the good ones were pruned. The limit was raised, tar's exit status is now checked, and `gzip -t` runs before the archive is fingerprinted or older copies are pruned. The older archives were restored from the mirror, whose dated deletion directory is what made that possible.
- **A YAML typo took the wallboard offline (November 2026).** An unescaped quote in an alert rule description made Prometheus exit on start and crash-loop for about a day, with the dashboards showing "No data". A crash-looping Prometheus cannot alert on itself. Validate rule edits with `promtool check rules` before restarting, and check `docker ps` if panels go empty.
- **Two monitors pointed at a pre-migration address.** After the July rebuild, two Uptime Kuma monitors targeted an address that belonged to neither machine. One was a duplicate, and the other, a GPU exporter check, had been silently down for five days ([0018](decisions/0018-nexus-monitoring-and-sensors.md)).
- **Netdata's Docker module burned CPU.** Its stock poll interval kept `dockerd` and `containerd` at about half a core. A 10-second override fixed it ([monitoring](services/monitoring.md#lessons)).
- **A removable drive stopped mounting after being re-plugged.** It had a plain fstab entry with no automount rule, so it only mounted if present at boot, and the copy to it quietly stopped for two weeks. It is now handled like the pool disks ([storage and parity](runbooks/storage-pool-and-parity.md)).
- **A zombie-process leak in a browser-based service.** Orphaned child processes accumulated because nothing reaped them. `init: true` in the Compose service, which runs Docker's built-in init as PID 1, brought the defunct count to zero.
- **A third-party management tool was removed.** Its backup drill failed four different ways and its `watch` daemon was replaced by two Prometheus rules ([0014](decisions/0014-prometheus-replaces-crash-loop-watch.md)).

## Grafana notes

Found while building the wallboard, and applicable generally.

- **Public dashboard links cache query results.** Values did not match a direct Prometheus query taken at the same moment. Anonymous access to the normal dashboard URL, scoped to the Viewer role, queries live. Use that where the numbers need to be current.
- **Every panel in a provisioned dashboard needs a unique `id`.** If it is missing or duplicated, the grid can collapse to render only the last panel, with no error or log line.
- **`autofitpanels` scales by the ratio between panels,** not by the absolute `gridPos.h` total. Scaling every panel's height uniformly changes nothing, so change the ratio between rows instead.
