# 0013. Hardware thermal limit at 90°C via TCC offset

Date: 2026-08-07 · Status: accepted

## Context

A nightly video-transcoding batch pushed about six cores to 619% CPU and the package to 94°C. A first attempt capped the container at five cores, then was reverted. The figure it was based on turned out to be a miscount, and at night nothing else uses the machine, so leaving three cores idle was the wrong trade.

The firmware left the temperature-target register writable, with a stock TCC activation offset of 6, meaning throttling starts at 94°C. That 94°C was the throttle line itself, not headroom below it.

## Decision

Use the CPU's own thermal management instead of a software cap or `thermald`. Set the TCC offset to 10, so the i5-9300H throttles at **90°C**. The Adaptive Thermal Monitor then trims frequency and voltage at the limit. There is no daemon, no conflict with TLP, and the response is instant.

It is written with `wrmsr` by a systemd unit at boot and again after suspend or hibernate, since thermal registers can silently reset across suspend.

## Evidence

The offset was stepped from 6 to 8 to 10 under `stress-ng --cpu 8` with `turbostat` logging every three seconds.

| Offset | Throttle line | Behavior under sustained load |
|---|---|---|
| 6 (stock) | 94°C | Overshoots to 95°C, then corrects hard to about 3500 MHz by 12 s |
| 8 | 92°C | Overshoots to 92°C, corrects to about 3550 MHz by 15 s |
| 10 | 90°C | Holds 3900 to 3974 MHz for about 21 s, then settles to about 3550 MHz |

This is the opposite of the naive expectation that a lower throttle line costs performance. Offset 10 kept boost clocks the longest, because the monitor trims smoothly at the line instead of overshooting and correcting hard. All three settings converge to the same sustained floor of about 3550 MHz, so the offset changes peak temperature and how long turbo holds, but not long-run throughput. Combined with the undervolt from [0001](0001-cpu-undervolting-and-thermals.md), heavy batches now run at about 90°C with no measured throughput cost.

## Alternatives rejected

- **`thermald`:** a software daemon with more moving parts than one register, and deliberately disabled here because of the TLP conflict.
- **A container CPU cap:** wastes idle cores and fights the wrong problem.
- **A systemd timer limiting the offset to the transcoder's run window:** the transcoder runs continuously as a watch-folder server and can pick up heavy jobs in daytime, so an always-on limit is more robust.

## Consequences

- The offset is global, so it also limits other all-core bursts. That is the intent.
- It needs the `msr` module and `msr-tools` at boot, which the unit loads.
- Not applicable to Citadel, whose firmware has no such lever and which runs cool.
