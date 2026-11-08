# 0018. Monitoring Nexus: one Netdata container, central Prometheus, explicit sensors

Date: 2026-09-05 · Status: accepted

## Context

Nexus had no telemetry, so the wallboard could not show it. It is also usually powered off, which constrains the design.

## Decision

**Run one Netdata container on Nexus and nothing else.** Legion's Prometheus scrapes it over Tailscale. A second Prometheus there is deliberately not deployed, because Legion already provides the central time-series database, alert evaluation, Grafana and Alertmanager. Duplicating that would add a stack to maintain on a machine that is rarely on.

Guardrails: a CPU limit of 0.3, a 256 MiB memory limit, read-only host telemetry mounts, a read-only Docker socket, and state persisted in a home directory. After deployment it used about 116 MiB and 1.8% CPU, and the Prometheus target was up.

**Add a small GPU exporter.** Netdata does not expose NVIDIA counters, so a minimal systemd service wraps `nvidia-smi` and serves utilization, memory, used and total VRAM, and temperature. It performs no GPU control operations, needs no credentials, bounds each call with a five-second timeout, and restarts automatically. Prometheus scrapes it every 15 seconds.

**Use activity colors for GPU utilization, not health colors.** High utilization is normal during rendering, gaming or LLM work, so it is green below 50% and blue above, never red. VRAM keeps separate capacity-oriented thresholds. Citadel has no NVIDIA telemetry and keeps a single CPU temperature card.

**Select explicit CPU sensors.** The first Nexus query took the maximum of every temperature sensor on the machine, so an unrelated board, storage or network sensor could appear as the CPU temperature. The panels now select the CPU package reading directly:

| Machine | Sensor |
|---|---|
| Legion | Intel `coretemp`, Package id 0 |
| Citadel | Intel `coretemp`, Package id 0 |
| Nexus | AMD `k10temp`, Tctl |

GPU temperatures come from the dedicated NVIDIA exporters.

## Evidence

Live readings at 1920×1080 were Legion at 60°C CPU and 50°C GPU, Citadel at 47°C, and Nexus at 54.8°C CPU and 70°C GPU. Nexus load and disk metrics appeared in Prometheus.

## Consequences

- A dashboard that picks "the highest sensor" can quietly show the wrong device. Name the sensor you mean.
- Colors should say what a number means. Activity and health are different messages.
- The layout built on top of this telemetry is in [0019](0019-wallboard-layout-evolution.md).
