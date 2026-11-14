#!/bin/bash
# Citadel connectivity watchdog (2026-08-11): the room extender is Citadel's
# only uplink and its backhaul can half-die (2026-08-10/11: outbound OK,
# inbound + tailnet dead all night). Recovery ladder:
#   gateway down  -> bounce wlan0 -> reboot if still dead
#   tailscale down (gw fine) -> restart tailscaled -> bounce wlan0 -> alert
#   tcp probe down (gw+ts fine) -> bounce wlan0 (no reboot)
# Silent when healthy; ntfy ping on action. Runs every 2 min. Root-only.
#
# TCP probe (2026-08-26): added after the room extender silently
# black-holed DSCP-marked TCP for ~5h while ICMP-to-gateway and tailscale
# both stayed green - the original two checks saw nothing and never
# alerted. A TCP probe to Legion over the tailnet catches this selective
# blackholing (gw ok + tcp dead is the signature); it feeds the same
# 2-strike ladder but never escalates to reboot on its own, and fires an
# immediate one-shot alert on first detection since a wifi bounce may not
# fix a DPI/DSCP block.

EVENT_HELPER="${PRODUCER_EVENTS_HELPER:-}"
if [ -n "$EVENT_HELPER" ] && [ ! -r "$EVENT_HELPER" ]; then
  EVENT_HELPER=""
fi
if [ -z "$EVENT_HELPER" ]; then
  for candidate in \
    /usr/local/lib/homelab-events/producer_event.sh \
    /opt/homelab/scripts/homelab-events/lib/producer_event.sh; do
    if [ -r "$candidate" ]; then
      EVENT_HELPER="$candidate"
      break
    fi
  done
fi
if [ -n "$EVENT_HELPER" ]; then
  . "$EVENT_HELPER"
fi

STATE="${CONN_WATCH_STATE:-/run/conn-watch.strikes}"
STATE_TCP="${CONN_WATCH_TCP_ALERTED_STATE:-/run/conn-watch.tcp-alerted}"
STATE_ACTION="${CONN_WATCH_ACTION_STATE:-/run/conn-watch.action}"
CONNECTIVITY_DEDUPE_KEY="connectivity-watch/citadel/network/citadel_wallboard_or_connectivity_self_heal"
# Defaults to the current default route, since the LAN address is set by DHCP.
GW="${CONN_WATCH_GATEWAY:-$(ip -4 route show default 2>/dev/null | awk '{print $3; exit}')}"
GATEWAY_PROBE_COUNT="${CONN_WATCH_GATEWAY_PROBE_COUNT:-3}"
GATEWAY_PROBE_TIMEOUT="${CONN_WATCH_GATEWAY_PROBE_TIMEOUT:-2}"
TCP_TARGET="${CONN_WATCH_TCP_TARGET:-100.64.0.1}"
TCP_PORT="${CONN_WATCH_TCP_PORT:-22}"
WIFI_DEVICE="${CONN_WATCH_WIFI_DEVICE:-wlan0}"
TAILSCALE_TIMEOUT="${CONN_WATCH_TAILSCALE_TIMEOUT:-8}"
TAILSCALE_RESTART_TIMEOUT="${CONN_WATCH_TAILSCALE_RESTART_TIMEOUT:-30}"
TAILSCALE_RECHECK_WAIT="${CONN_WATCH_TAILSCALE_RECHECK_WAIT:-60}"
EVENT_EMIT_TIMEOUT="${CONN_WATCH_EVENT_TIMEOUT:-10}"

log() {
  printf 'conn-watch: %s\\n' "$*"
}

tailscale_online() {
  local status_json status
  status_json=$(timeout "$TAILSCALE_TIMEOUT" tailscale status --json 2>/dev/null) || return 1
  status=$(printf '%s' "$status_json" | python3 -c 'import json,sys
try: print(json.load(sys.stdin)["Self"]["Online"])
except Exception: print("false")' 2>/dev/null)
  [ "$status" = "True" ]
}

gateway_reachable() {
  # A single lost ICMP reply is common while the extender is busy. Require
  # multiple probes, but accept the gateway as reachable if any probe replies.
  ping -c "$GATEWAY_PROBE_COUNT" -W "$GATEWAY_PROBE_TIMEOUT" "$GW" >/dev/null 2>&1
}

emit_event_remote_bounded() {
  [ -n "$EVENT_HELPER" ] || return 0
  timeout "$EVENT_EMIT_TIMEOUT" bash -c '
    helper="$1"
    shift
    . "$helper"
    emit_event_remote "$@"
  ' _ "$EVENT_HELPER" "$@" >/dev/null 2>&1 || true
}

record_degraded_event() {
  if command -v emit_event_remote >/dev/null 2>&1; then
    emit_event_remote_bounded citadel-conn-watch citadel connectivity-watch network citadel_wallboard_or_connectivity_self_heal failure warning digest "$1" "$CONNECTIVITY_DEDUPE_KEY"
  fi
  echo 1 > "$STATE_ACTION"
}
bounce_wifi() {
  log "reassociating Wi-Fi device $WIFI_DEVICE"
  nmcli dev disconnect "$WIFI_DEVICE" 2>/dev/null || return 1
  sleep 3
  nmcli dev connect "$WIFI_DEVICE" 2>/dev/null
}

gw_ok=1
gateway_reachable || gw_ok=0

ts_ok=1
if ! tailscale_online; then ts_ok=0; fi

tcp_ok=1
timeout 5 bash -c "exec 3<>/dev/tcp/$TCP_TARGET/$TCP_PORT" 2>/dev/null || tcp_ok=0

# Selective-blackhole signature: gateway (ICMP) fine but TCP to Legion dead.
# One-shot alert the first time it's seen in a streak - independent of the
# strike ladder below, since a wifi bounce may not fix a DPI/DSCP block.
if [ "$gw_ok" -eq 1 ] && [ "$tcp_ok" -eq 0 ]; then
  if [ "$(cat "$STATE_TCP" 2>/dev/null || echo 0)" -eq 0 ]; then
    record_degraded_event "Citadel link degraded: TCP probe to Legion failed (selective blackhole?)"
  fi
  echo 1 > "$STATE_TCP"
else
  echo 0 > "$STATE_TCP"
fi

# The gateway is a local ICMP signal, while the tailnet/TCP probes exercise
# the path used by the mirror and remote services. Never tear down a working
# mirror path because a busy extender dropped only a gateway probe.
if [ "$gw_ok" -eq 0 ] && [ "$ts_ok" -eq 1 ] && [ "$tcp_ok" -eq 1 ]; then
  log "gateway probe lost while Tailscale and TCP path are healthy; suppressing Wi-Fi bounce"
  echo 0 > "$STATE"
  exit 0
fi

# Healthy: gateway reachable AND tailscale online AND tcp probe ok -> reset strikes
if [ "$gw_ok" -eq 1 ] && [ "$ts_ok" -eq 1 ] && [ "$tcp_ok" -eq 1 ]; then
  if [ "$(cat "$STATE_ACTION" 2>/dev/null || echo 0)" -eq 1 ]; then
    if command -v emit_event_remote >/dev/null 2>&1; then
      emit_event_remote_bounded citadel-conn-watch citadel connectivity-watch network citadel_wallboard_or_connectivity_self_heal resolved info digest "Citadel connectivity recovered after self-heal." "$CONNECTIVITY_DEDUPE_KEY"
    fi
    echo 0 > "$STATE_ACTION"
  fi
  echo 0 > "$STATE"
  exit 0
fi

# Degraded: 1st check records a strike, 2nd consecutive triggers action
strikes=$(( $(cat "$STATE" 2>/dev/null || echo 0) + 1 ))
echo "$strikes" > "$STATE"
[ "$strikes" -lt 2 ] && exit 0
echo 0 > "$STATE"

if [ "$gw_ok" -eq 0 ]; then
  record_degraded_event "Gateway unreachable - bouncing $WIFI_DEVICE..."
  log "gateway probe failed; bouncing $WIFI_DEVICE"
  bounce_wifi
  sleep 90
  if ! gateway_reachable; then
    record_degraded_event "Still unreachable after $WIFI_DEVICE bounce - rebooting Citadel"
    log "gateway still unreachable after Wi-Fi bounce; rebooting Citadel"
    sleep 5
    systemctl reboot
  fi
elif [ "$ts_ok" -eq 0 ]; then
  # gateway fine but tailscale down: restart the daemon, then bounce wifi
  record_degraded_event "Tailscale offline (gw OK) - restarting tailscaled..."
  log "Tailscale probe failed; restarting tailscaled"
  if ! timeout "$TAILSCALE_RESTART_TIMEOUT" systemctl restart tailscaled; then
    log "tailscaled restart exceeded ${TAILSCALE_RESTART_TIMEOUT}s"
  fi
  sleep "$TAILSCALE_RECHECK_WAIT"
  if ! tailscale_online; then
    record_degraded_event "Tailscale still offline after daemon restart - bouncing $WIFI_DEVICE..."
    log "Tailscale still offline; bouncing $WIFI_DEVICE"
    bounce_wifi
  fi
else
  # gateway + tailscale fine but tcp probe still failing after 2 strikes:
  # bounce wifi only - no reboot, since ICMP/UDP prove the link is up
  record_degraded_event "TCP probe to Legion still failing (2 strikes) - bouncing $WIFI_DEVICE..."
  log "TCP probe failed with gateway and Tailscale up; bouncing $WIFI_DEVICE"
  bounce_wifi
fi
