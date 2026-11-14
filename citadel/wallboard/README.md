# Wallboard

A native Python kiosk for Citadel, controlled from Home Assistant. It rotates a clock, the Grafana wallboard dashboard and a kan.bn task board on the Beacon monitor. The service description is in [docs/services/wallboard.md](../../docs/services/wallboard.md).

| Path | Role |
|---|---|
| `src/server.py` | Page server on port 8080. Serves the pages, `/current` (the page to show) and the task list |
| `src/wallboard-rotation.py` | Writes the current page from a time-of-day schedule or a round-robin rotation. The two are mutually exclusive |
| `src/heimdall_actions.py` | The one hardware and configuration layer: page, brightness, monitor power, screenshot, rotation, schedule, tasks and Nexus power. Everything else calls it |
| `src/control_server.py` | Authenticated API for Home Assistant. Listens only on the tailnet address and needs a bearer token |
| `src/kanbn_client.py` | Small client for the kan.bn API, with stable card IDs for mutations |
| `src/paths.py` | Paths for config and runtime state, overridable by environment |
| `web/pages/` | The kiosk pages. `index.html` cross-fades between two iframes and waits for the incoming page to say it is ready |
| `config/*.json.example` | Examples of rotation and schedule data |

## Configuration

Copy the examples to `config/rotation.json` and `config/schedule.json`. Override locations with `WALLBOARD_CONFIG_DIR`, `WALLBOARD_VAR_DIR` and `KANBN_ENV_FILE`. The control server reads its token from the file named by `HEIMDALL_TOKEN_FILE` (at least 32 characters, mode 0600) and its listen address from `HEIMDALL_BIND_HOST`. kan.bn access uses `KAN_API_KEY`, `KAN_BOARD_PUBLIC_ID` and optionally `KAN_API_BASE_URL`.

The Grafana page embeds a provisioned dashboard and relies on Grafana allowing anonymous viewing, scoped to the Viewer role ([wallboard](../../docs/services/wallboard.md)).

## Design notes

- **One shared actions module.** The rotation loop, the control API and the Home Assistant integration all call `heimdall_actions.py`, so there is no second copy of the state. After editing it, restart every service that imports it.
- **Degrades by part.** If kan.bn is unreachable, the task state is empty and the API reports a degraded component, and display control keeps working.
- **No manual override gets stomped.** The rotation loop writes the page only when a schedule block or rotation tick changes, so a manual choice made between transitions stays until the next boundary.
- **Brightness is software gamma** through `wl-gammarelay-rs`, floored at 10% so the screen cannot be dimmed to black by accident. Power is a separate control.
- **The page server listens on all interfaces,** because it was built for a kiosk on the same machine. It serves only the pages and the task list, but binding it to `127.0.0.1` would be tighter.
