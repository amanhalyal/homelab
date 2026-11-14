# Wallboard (Heimdall)

A kiosk display driven by Citadel and shown on an external monitor, the Beacon. It rotates a clock, the Grafana dashboard and a task board, and it is controlled from Home Assistant. This page covers what it is and how it is controlled. Source is under [`citadel/`](../../citadel/README.md). The display behavior is in [0017](../decisions/0017-wallboard-display-control.md), the reliability fixes in [0011](../decisions/0011-wallboard-reliability.md) and the layout in [0019](../decisions/0019-wallboard-layout-evolution.md).

## How it works

- A Firefox kiosk shows pages served by a small standard-library Python HTTP server on Citadel.
- Pages: a clock, the Grafana dashboard and a task board backed by a kan.bn instance on Legion. A rotation service cycles pages, and a schedule service sets the page and brightness for time blocks, for example dimming overnight.
- Source is in `citadel/wallboard/`. The server, rotation and kiosk are native user systemd services, not containers, because they need the display session.
- The Grafana page embeds a provisioned dashboard and needs anonymous viewing enabled on Grafana. This is a deliberate exception, scoped to the Viewer role, to the general rule against anonymous access.

## Control through Home Assistant

Control moved to a Home Assistant dashboard on 2026-10-16, replacing a Telegram bot and mini app. The control surface has three tabs: wallboard, tasks and settings.

| Control | Purpose |
|---|---|
| Beacon light | Screen power and software brightness, floored at 10% |
| Page select | Clock, Grafana or tasks. A manual choice keeps schedule and rotation enabled, and the next block or tick takes over |
| Rotation switch and interval | Rotate pages every 10 to 86,400 seconds. Enabling it disables the schedule |
| Schedule switch | Time blocks. Enabling it disables rotation |
| Screenshot camera | A live image of the Beacon, taken with `grim` and proxied through authenticated Home Assistant |
| Task lists | The board's columns as to-do lists. Checking an item advances it one column |
| Nexus buttons | Wake, sleep, restart and cancel a pending power action, through Citadel's existing scripts. Shut down is disabled by default |

An advanced `heimdall.command` action accepts a command and a JSON payload for schedule, rotation and task changes.

## Architecture

- A control server on Citadel (`heimdall-control.service`) listens only on its tailnet address. Tailscale encrypts the host-to-host hop, and every mutation and screenshot requires a random bearer token. Only `/health` answers without one.
- The token is private runtime state on Citadel and in Home Assistant's `secrets.yaml`, both mode 0600, never committed or printed.
- A shared module, `heimdall_actions.py`, is the single hardware and configuration layer. The control server, the rotation loop and the Home Assistant integration all call it. A second module owns task-board mutations by stable card ID. Nothing keeps a separate data model.
- The Home Assistant integration is a custom component with coordinated polling. If the task board is unreachable, only the task entities degrade, and display control keeps working. If Citadel is down, the integration shows as unavailable and retries.
- The health-check timer publishes the wallboard, server, rotation, kiosk and API state to Pushgateway. After the migration, the push switched to `PUT` for the job group, which removed the retired bot's series, so the generic service-down rule monitors the current units.

## Retiring the Telegram bot

The bot, the mini app, its credential file, the reminder timer and the certificate-renewal units were removed. The bot's commands, menu and webhook were cleared through the Telegram API, and the single tailnet route to the mini app was removed. The live credential file was deleted, not copied into the rollback archive. Pre-cutover source and units are kept in an ignored archive directory. Rolling back would mean restoring those and provisioning a fresh credential, which is not the intended path.

## Storage on the wallboard

The Grafana page shows Atlas and Aegir as Legion data disks, Arcadia's pooled capacity and Argus's parity usage, in a 2×2 grid at the center. Detailed parity state stays in the operations dashboard and the digest and is not shown on the wall.

## Recovery

To disable Home Assistant control, disable the integration and stop `heimdall-control.service`. The native wallboard and its schedule continue unchanged. After editing the shared actions module, restart every service that imports it ([0017](../decisions/0017-wallboard-display-control.md)). Refresh deployment copies with `rsync` or `docker cp`, validate Home Assistant with its `check_config` script, then restart only the service that changed. Never deploy a whole clean config tree over a live Home Assistant config or its `.storage`.
