# 0010. Scrutiny for SMART disk health, one dashboard for all machines

Date: 2026-07-29 · Status: accepted

## Context

Drive health was checked by hand with `smartctl` and `hdparm`, on request, with no history and no dashboard. That became obvious while verifying the head-parking fix in [0002](0002-hdd-power-and-readahead.md). `smartd` gave basic threshold alerts, but no trends.

## Decision

Run Scrutiny's all-in-one image, which bundles the web UI, its own InfluxDB and the collector, on Legion. The hub and spoke split is unnecessary complexity for a single host. The image is pinned to a tagged release and not the nightly channel, a lesson learned when an unpinned Netdata image once silently broke a collector.

It monitors Atlas and the NVMe boot drive, using the image's default daily collection schedule. Spinning-disk wear metrics gain nothing from running more often.

Later the same day it was extended to Citadel with a collector-only container that reports to Legion's hub through `COLLECTOR_API_ENDPOINT`. That gives one dashboard covering all four drives on both machines, instead of a second disconnected stack. Doing so required publishing the hub's port on Legion on a free host port, since the usual one was taken by another process, and setting `COLLECTOR_RUN_STARTUP=true` so a fresh container reports immediately and does not wait for the daily run.

## Gotcha

Passing only the NVMe namespace device (`/dev/nvme0n1`) into the container was not enough. `smartctl --scan` also needs the controller character device `/dev/nvme0`. Without it the collector silently found only the HDD. The fix was confirmed by running `smartctl --scan --json` inside the container, not by checking that the container started.

## Evidence

A manually triggered collection on Citadel showed all four drives in the single summary returned by Legion's API.

## Consequences

The UI is reachable only inside the Docker network and over the tailnet, with no proxy host yet. Add one if regular access outside SSH is wanted.
