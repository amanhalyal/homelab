#!/usr/bin/env bash
set -u

STATUS_PATH=${CITADEL_DISPLAY_STATUS_PATH:-/sys/class/drm/card1-HDMI-A-1/status}
POLL_INTERVAL=${CITADEL_DISPLAY_POLL_INTERVAL:-5}
STATE_DIR=${CITADEL_DISPLAY_STATE_DIR:-/home/kiosk/.local/state}
STATE_FILE="$STATE_DIR/citadel-display-mode"
SYSTEMCTL=${CITADEL_SYSTEMCTL:-systemctl}
HYPRCTL=${CITADEL_HYPRCTL:-hyprctl}
RUN_ONCE=${CITADEL_DISPLAY_RUN_ONCE:-0}
OUTPUT=${CITADEL_DISPLAY_OUTPUT:-HDMI-A-1}
DOCKED_UNITS=(wallboard-server.service wallboard-rotation.service wallboard-kiosk.service)
mkdir -p "$STATE_DIR"

log() {
  printf 'citadel-display-mode: %s\n' "$*"
}

# Re-enable the external output after an HDMI power/hotplug event. Hyprland can
# retain a disabled output even after DRM reports the connector as connected.
ensure_external_output() {
  local monitors
  if ! monitors=$("$HYPRCTL" monitors -j 2>/dev/null); then
    log "Hyprland is not ready; will retry output recovery"
    return 1
  fi
  if /usr/bin/python3 -c 'import json,sys; name=sys.argv[1]; raise SystemExit(not any(m.get("name") == name for m in json.load(sys.stdin)))' "$OUTPUT" <<<"$monitors"; then
    return 0
  fi
  log "re-enabling $OUTPUT"
  "$HYPRCTL" keyword monitor "$OUTPUT,preferred,auto,1" >/dev/null
}

start_docked_services() {
  "$SYSTEMCTL" --user start "${DOCKED_UNITS[@]}"
}

stop_docked_services() {
  "$SYSTEMCTL" --user stop wallboard-kiosk.service wallboard-rotation.service wallboard-server.service
}

# Only the kiosk unit sets this marker. Run this fallback cleanup once when
# entering handheld mode; KillMode=control-group handles the normal case.
stop_wallboard_firefox() {
  /usr/bin/python3 - <<'PY'
import os
import signal
import time

MARKER = b"CITADEL_WALLBOARD=1"

def marked_pids():
    found = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return found
    for name in entries:
        if not name.isdigit():
            continue
        try:
            env = open(f"/proc/{name}/environ", "rb").read().split(b"\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
            continue
        if MARKER in env:
            found.append(int(name))
    return found

def send(pids, sig):
    for pid in pids:
        try:
            os.kill(pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

targets = marked_pids()
if targets:
    send(targets, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if not any(os.path.exists(f"/proc/{pid}") for pid in targets):
            break
        time.sleep(0.1)
    send([pid for pid in targets if os.path.exists(f"/proc/{pid}")], signal.SIGKILL)
PY
}

last=""
while :; do
  status=disconnected
  if [[ -r "$STATUS_PATH" ]]; then
    status=$(<"$STATUS_PATH")
    [[ "$status" == connected ]] || status=disconnected
  fi

  if [[ "$status" == connected ]]; then
    # Reconcile continuously, not only on a connector transition: Beacon may
    # lose power while DRM keeps reporting connected, and units may fail later.
    if ! ensure_external_output; then
      : # Hyprland session startup can race this service; retry next poll.
    fi
    if [[ "$last" != connected ]] || ! "$SYSTEMCTL" --user --quiet is-active "${DOCKED_UNITS[@]}"; then
      printf '%s docked\n' "$(date --iso-8601=seconds)" >"$STATE_FILE"
      start_docked_services
    fi
  elif [[ "$last" != disconnected ]]; then
    printf '%s handheld\n' "$(date --iso-8601=seconds)" >"$STATE_FILE"
    stop_docked_services
    stop_wallboard_firefox
  fi

  last=$status
  [[ "$RUN_ONCE" == 1 ]] && break
  sleep "$POLL_INTERVAL"
done
