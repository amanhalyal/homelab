# 0019. Wallboard layout: iterate by screenshot, then remove the switching logic

Date: 2026-09-05 · Status: accepted

The Grafana dashboard on the wallboard has a fixed 1920×1080 canvas and has to be readable from across a room. This record summarizes how its layout settled, since the order of the changes is the useful part.

## What happened

1. **Two columns,** Legion and Citadel, with CPU, RAM, temperature, disk and network panels. Legion's GPU utilization and temperature were not useful as permanent wall signals, so the GPU panels were replaced with root disk usage, and unhealthy-container and uptime panels were replaced with 1, 5 and 15 minute load cards for each host. The load labels are short (`L-1`, `C-5`, `N-15`) because the cards are narrow.
2. **A first three-column attempt failed.** Stretching Grafana's grid from 24 units to 36 produced unreadable stat values, displaced rows and large black areas. A screenshot review rejected it, and the draft was discarded.
3. **The accepted geometry stays inside the native 24-unit grid,** with each host taking 8 units. Per host: CPU and RAM as two 4-unit cards, temperature and disk as two 4-unit cards, three 2-unit load cards, and one 8-unit network panel.
4. **Automatic two and three-column switching.** Nexus is usually off, so two dashboards were provisioned and a small service on Citadel probed Nexus every 15 seconds. It needed three consecutive successes to switch to three columns and three consecutive failures to go back, which prevents flapping during boot, shutdown or a network blip. It changed only the Grafana iframe selector, so the clock and other pages were unaffected.
5. **Host headers, shared storage and generic titles.** A thin header row names each column. A header made from HTML was sanitized away and a large Markdown heading was clipped, so a smaller native Markdown heading in a slightly taller row was used. The per-host storage cards hid the important backup mounts, so two shared gauges for Atlas and Aegir replaced them. Repeated host prefixes were dropped from panel titles once headers existed.
6. **Final simplification.** The two-column dashboard and the availability watcher were removed. Nexus being off now simply leaves its fixed column without fresh values. No dashboard swap and no offline placeholder are needed, so there is less to break. Refresh is 10 seconds, and alert evaluation stays at 30 seconds to avoid churn.
7. **Small refinements.** Uptime became the first metric in each host's top row, shown as whole hours with a literal `h`, because Grafana's automatic units switched to days and weeks. The load cards were kept after a load-free trial, since they are a useful dynamic health signal. A one-unit gutter experiment was reverted after screenshots showed oversized gaps.

## Final layout

```text
          LEGION                CITADEL               NEXUS
Row 1     UPTIME | CPU | RAM    UPTIME | CPU | RAM     UPTIME | CPU | RAM
Row 2     CPU TEMP | GPU TEMP   TEMP | DISK %          CPU TEMP | GPU TEMP
Row 3     L-1 | L-5 | L-15      C-1 | C-5 | C-15       N-1 | N-5 | N-15
Row 4     GPU % | VRAM %        ATLAS | AEGIR           GPU % | VRAM %
Row 5     NETWORK               NETWORK                NETWORK
```

## Lessons

- At a fixed resolution, a screenshot is the test. Every layout step here was accepted or rejected by looking at the real output at 1920×1080.
- Hysteresis, requiring several consecutive probes before acting, is the right way to avoid flapping. But the best fix was deleting the switch, since a column that is simply empty needs no logic.
- Sensors and exporters behind these panels are described in [0018](0018-nexus-monitoring-and-sensors.md).
