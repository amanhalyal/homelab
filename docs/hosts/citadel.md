# Citadel

The light appliance: an always-on laptop that drives the wallboard, wakes Nexus, and runs a few monitoring containers. It is deliberately small. Heavy and stateful applications belong to [Legion](legion.md).

## Identity

| | |
|---|---|
| Hardware | HP laptop, Intel i3-8145U (2c/4t) |
| OS | CachyOS, Arch-based. Lid-close suspend is disabled |
| Access | SSH, key only |
| Docker project | `valhalla` |

## Network

| Interface | Purpose |
|---|---|
| `wlan0` | 2.4 GHz Wi-Fi. The only path to the router, the LAN and the internet |
| `eno1` | A point-to-point cable to Nexus on its own subnet. Used for Wake-on-LAN and fast transfers only. It does not reach the router or Legion |

**Wi-Fi resilience.** A root timer runs a connection watch every two minutes. Its Tailscale probes are bounded, and it needs two consecutive failures before acting. A gateway-only failure can escalate to a reboot after a failed Wi-Fi bounce. A Tailscale or TCP-only failure never reboots the host. A separate timer re-associates Wi-Fi every Monday at 05:30 as a precaution.

## Docker

One Compose project, entered through `citadel/docker-compose.yml`, with two category files, `monitoring` and `ops`. Run it from `~/homelab/citadel`. The `.env` is mode 0600 and host-local.

| Container | Role |
|---|---|
| `netdata` | Host telemetry, on the host network, scraped by Legion's Prometheus |
| `scrutiny-collector` | SMART collector for the NVMe drive, reporting to Legion |
| `watchtower` | Image updates. Per machine, so Legion's instance cannot see this daemon |
| `healthcheck-pinger` | An external liveness heartbeat |

The rationale for running Docker here at all is in [0009](../decisions/0009-docker-on-citadel.md).

## Native services

Services that need the display, raw network access or the host's user model stay native:

- The wallboard server, rotation and kiosk are **user** systemd services. The control API and the health check are system services. See [the wallboard](../services/wallboard.md).
- A health-check timer runs every two minutes and pushes service state to Legion's Pushgateway over SSH. The SSH hop is bounded, so a network stall cannot leave a permanently stale dead-man metric.
- Beacon software brightness is a user service, `wl-gammarelay`, driven over DBus. The laptop panel uses `brightnessctl`.

```bash
systemctl --user status wallboard-server.service wallboard-rotation.service wallboard-kiosk.service
sudo systemctl status heimdall-control.service heimdall-healthcheck.timer
```

The wallboard tree is split by responsibility: `src/` for Python, `web/` for browser assets, `config/` for live settings and secrets, `var/` for mutable state, and `archive/` for rollback material. Git tracks safe examples and ignores the live contents of `config/`, `var/` and `archive/`.

## Displays

The laptop panel is `eDP-1` and the external wallboard monitor is `HDMI-A-1`, called the Beacon. The behavior is in [0017](../decisions/0017-wallboard-display-control.md):

- **Idle and lid policy.** After 15 minutes unlocked, or on lid close, only the laptop panel turns off. The Beacon, kiosk, session and network keep running. Lid open or activity restores the panel, and a closed lid wins.
- **Handheld mode.** When HDMI is unplugged, a controller stops the docked-only wallboard services, polls cheaply every 10 seconds, and records `docked` or `handheld` in a state file.
- **Wi-Fi guard.** Firmware sets a WLAN soft block when the lid closes. A root service clears only that soft block, leaving hard blocks and other radios alone.

### Boot recovery and unplugging the Beacon

Sometimes when Citadel and the Beacon get power back at the same moment, the graphics driver's hot-plug detection stays low, so the Beacon looks disconnected although it is plugged in. A root oneshot runs before the login manager. It reads the last recorded mode and forces the connector on **only** when that mode is exactly `docked`. A prior `handheld`, a missing file or a malformed one does nothing.

If the Beacon will be physically unplugged while Citadel is off, set handheld intent before shutting down so the next boot does not force a nonexistent display:

```bash
systemctl --user stop citadel-display-mode.service
systemctl --user stop wallboard-kiosk.service wallboard-rotation.service wallboard-server.service
printf '%s handheld\n' "$(date --iso-8601=seconds)" > ~/.local/state/citadel-display-mode
sudo systemctl poweroff
```

Stopping the controller first matters. Otherwise its docked reconciliation can restart the units and rewrite the docked intent before shutdown.

### Quick display check

```bash
hyprctl monitors -j
loginctl show-session <id> -p State -p LockedHint
systemctl --user is-active citadel-display-mode.service citadel-lid-panel.service hypridle.service \
  wallboard-server.service wallboard-rotation.service wallboard-kiosk.service
```

In the panel-off state, `eDP-1` reports DPMS off and `HDMI-A-1` reports it on. A physical lid close and open cycle is the remaining acceptance test, since fixtures cannot prove it.

## Controlling Nexus

Scripts on Citadel wake, sleep, restart, shut down or cancel a pending power action on Nexus, all over the dedicated link:

```bash
~/homelab/citadel/scripts/pc-control/wake-nexus.sh
~/homelab/citadel/scripts/pc-control/sleep-nexus.sh
~/homelab/citadel/scripts/pc-control/restart-nexus.sh
~/homelab/citadel/scripts/pc-control/shutdown-nexus.sh
~/homelab/citadel/scripts/pc-control/abort-nexus.sh
```

Wake uses the packaged `wakeonlan` client on the wired broadcast address. The script checks that the broadcast is routed through `eno1` and fails loudly otherwise. Citadel wakes Nexus and never the reverse, since Citadel is always on.

## Storage

Only the internal NVMe. Aegir used to be attached here as a mirror target, and it moved to Legion on 2026-10-31. Netdata and Scrutiny were adjusted to start without it.
