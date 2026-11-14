#!/usr/bin/env bash
set -euo pipefail

STATE_FILE=${CITADEL_DISPLAY_STATE_FILE:-/home/kiosk/.local/state/citadel-display-mode}
STATUS_PATH=${CITADEL_HDMI_STATUS_PATH:-/sys/class/drm/card1-HDMI-A-1/status}

log() {
  printf 'citadel-hdmi-boot-recovery: %s\n' "$*"
}

if [[ ! -r "$STATE_FILE" ]]; then
  log "no persisted intent; leaving HDMI connector unchanged"
  exit 0
fi

read -r _ mode _ <"$STATE_FILE" || mode=""
if [[ "$mode" != docked ]]; then
  if [[ "$mode" == handheld ]]; then
    log "prior intent is handheld; leaving HDMI connector unchanged"
  else
    log "persisted intent is invalid; leaving HDMI connector unchanged"
  fi
  exit 0
fi

if [[ ! -w "$STATUS_PATH" ]]; then
  log "cannot write HDMI connector status at $STATUS_PATH"
  exit 1
fi

log "prior intent is docked; forcing HDMI connector on"
printf 'on\n' >"$STATUS_PATH"
