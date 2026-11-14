# Citadel

Configuration and source for Citadel, the always-on appliance that drives the wallboard. The host is described in [docs/hosts/citadel.md](../docs/hosts/citadel.md), and the decisions behind it are in the [decision records](../docs/decisions/README.md).

| Path | Contents |
|---|---|
| `docker-compose.yml`, `compose/` | The `valhalla` Compose project: Netdata, a Scrutiny collector, Watchtower and a heartbeat pinger |
| `wallboard/` | The kiosk: page server, rotation loop, Home Assistant control API, pages and example config. See [wallboard/README.md](wallboard/README.md) |
| `systemd/` | Units for the wallboard, the display-mode controller, lid and panel handling, the Wi-Fi guard and bounce, the connectivity watchdog and the health-check timer |
| `scripts/bin/` | The scripts those units run |
| `scripts/pc-control/` | Wake, sleep, restart, shutdown and abort for Nexus |
| `greetd/`, `firefox/` | Console autologin into the Wayland session, and a user stylesheet that enlarges the storage cards for the kiosk |

## Placeholders

The files use stand-in values for anything that identifies a real machine or account:

- User `kiosk` and checkout path `/opt/homelab`. Substitute the real account and location in the units and scripts.
- `100.64.0.1` is Legion's Tailscale address and `100.64.0.2` is Citadel's.
- `NEXUS_MAC` and `NEXUS_BROADCAST` are set in `systemd/heimdall-control.service` as placeholders, and the wake script refuses to run without them.
- `.env.example` lists the three variables the Compose project reads. The heartbeat ping URL is treated as a secret, because anyone holding it can fake a heartbeat.

## Running the Compose project

```bash
cd citadel
cp .env.example .env     # then fill it in
docker compose config -q
docker compose up -d
```

The wallboard, its control API, the rotation loop and the kiosk are native user services, not containers, because they need the Wayland display session. The Compose project holds only host telemetry and maintenance.

## How the pieces fit

- `citadel-display-mode` watches the HDMI connector. When the Beacon is connected it starts the docked units (page server, rotation, kiosk), and when it is not it stops them and cleans up any stray kiosk browser. It persists its intent so [`citadel-hdmi-boot-recovery`](scripts/bin/citadel-hdmi-boot-recovery.sh) can restore the forced-on HDMI state at boot.
- `citadel-lid-watch` and `citadel-panel-dpms` keep the laptop panel off while the lid is closed without touching the Beacon ([0017](../docs/decisions/0017-wallboard-display-control.md)).
- `citadel-wifi-rfkill-guard` clears the WLAN soft block that firmware sets when the lid closes. It leaves hard blocks and other radios alone.
- `conn-watch.sh` is the connectivity watchdog. It escalates from a Wi-Fi bounce to a reboot for a dead gateway, from a `tailscaled` restart to a bounce for a dead tailnet, and only bounces Wi-Fi when a TCP probe to Legion fails while ping and Tailscale are fine. That last case is the DSCP black hole in [known issues](../docs/known-issues.md#citadels-wi-fi-uplink-is-the-weakest-link).
- `heimdall-healthcheck.sh` pushes service state to Pushgateway on Legion, which feeds the `HeimdallServiceDown` and `CitadelUnreachable` alerts ([monitoring configuration](../compose/config/README.md)).

## Not published

- Test suites.
- A habit tracker page and its storage, which are personal.
- Retired experiments, older X11-era display scripts, and the Samba share.
