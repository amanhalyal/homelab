# Decision records

Short records of why the homelab is the way it is. Each one states the context, what was decided, the evidence behind it, and the consequences, including the cases where an assumption turned out to be wrong.

Records are numbered in the order they were made. They are being published in batches, so entries without a link are coming next.

| # | Decision | Area |
|---|---|---|
| [0001](0001-cpu-undervolting-and-thermals.md) | CPU undervolting and thermal tuning | Power and thermals |
| [0002](0002-hdd-power-and-readahead.md) | HDD head-parking and read-ahead tuning | Storage |
| [0003](0003-zram-and-swappiness.md) | zram at 25% of RAM, lower swappiness | Memory |
| [0004](0004-reverse-proxy-hardening-and-redundancy.md) | Reverse proxy: harden everywhere, reject redundancy | Network |
| [0005](0005-ssh-key-only-authentication.md) | SSH password authentication disabled | Security |
| [0006](0006-backup-verification.md) | Verify backups with checksums, not "the sync ran" | Backup |
| [0007](0007-migration-restore-gaps.md) | Migration restore: verify the claim, not an adjacent check | Operations |
| [0008](0008-cron-timezone.md) | Cron timing: set the system timezone, not `CRON_TZ` | Operations |
| [0009](0009-docker-on-citadel.md) | Run Citadel's services in Docker, keep display-bound parts native | Platform |
| 0010 | Scrutiny for SMART disk health | Monitoring |
| 0011 | Wallboard reliability: native service and load races | Wallboard |
| 0012 | Game server idle memory and render distance | Services |
| [0013](0013-tcc-thermal-offset.md) | Hardware thermal limit at 90°C via TCC offset | Power and thermals |
| 0014 | Prometheus rules replace a crash-loop watcher | Monitoring |
| 0015 | GPU offload for local LLMs | Local LLM |
| 0016 | Alert triage with a local classifier | Local LLM |
| 0017 | Wallboard display and brightness control | Wallboard |
| 0018 | Nexus monitoring and temperature sensors | Monitoring |
| 0019 | Wallboard layout evolution | Wallboard |
| [0020](0020-backup-pipeline-incidents.md) | Backup pipeline incidents and hardening | Backup |
| [0021](0021-minecraft-dual-stack-dns.md) | Give the game-server network IPv6 instead of pinning DNS | Network |
| 0022 | Citadel-unreachable alert and open incidents in the digest | Monitoring |

Some records describe parts that were later replaced, such as the nightly Atlas to Aegir mirror, which SnapRAID parity superseded. They say so at the top and are kept because the reasoning still applies.
