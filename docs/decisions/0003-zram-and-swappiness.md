# 0003. zram at 25% of RAM, lower swappiness

Date: 2026-07-22 · Status: accepted

## Context

Both machines used swap during normal operation: about 378 MB on Citadel, which ran 40 or more containers on 11 GB of RAM, and about 571 MB on Legion, mostly from a 12 GB Java heap on 22 to 24 GB of RAM.

## Decision

Use `systemd-zram-generator` with zstd compression and swap priority 100, so the kernel prefers it over disk swap. Size it at **25% of RAM** instead of the commonly cited 50%.

The size is a ceiling, not a reservation. Real memory use scales with what is actually compressed and is close to zero when idle. 25% covers current swap use many times over and keeps the worst case lower if zram ever fills with poorly compressible data.

On 2026-07-31, swappiness on Legion was also lowered from 60 to 15. After six days of uptime, diffuse cold pages across long-running containers had filled about 8 GB of swap despite plenty of available RAM, and a lower value makes the kernel swap less eagerly instead of relying on manual clears.

## Gotchas

- **The configured algorithm may not apply.** On Legion the first start came up with `lzo-rle` instead of zstd. The device has to be reset and restarted before the setting takes. Always check the `ALGORITHM` column in `zramctl`.
- **`swapoff -a` silently drops zram, and `swapon -a` does not bring it back.** zram is managed by its own systemd unit, not by fstab. A plain restart of that unit then fails with "Can't change algorithm for initialized device". The fix is `echo 1 > /sys/block/zram0/reset` followed by restarting `systemd-zram-setup@zram0.service`.
- **A rebuild loses it.** Neither machine carried zram through the July rebuilds, which is why the configuration now lives in the repo's setup notes.

## Consequences

Any future `swapoff -a` needs a manual zram reset and restart afterward, and the swap total from `swapon --show` should be checked against what is expected.
