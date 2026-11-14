#!/usr/bin/env bash
# Canonical event adapter for cron/systemd producers.
# This file never posts to ntfy. Shadow mode remains the safe default; the
# live cutover sets HOMELAB_EVENTS_MODE=canonical in each producer scheduler.

_PRODUCER_EVENTS_SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# The helper lives in <root>/lib/. A flat install (helper beside the code) is
# also accepted, so an installed copy can be relocated freely.
case "$_PRODUCER_EVENTS_SELF_DIR" in
  */lib) _PRODUCER_EVENTS_DEFAULT_ROOT="${_PRODUCER_EVENTS_SELF_DIR%/lib}" ;;
  *) _PRODUCER_EVENTS_DEFAULT_ROOT="$_PRODUCER_EVENTS_SELF_DIR" ;;
esac
PRODUCER_EVENTS_ROOT="${PRODUCER_EVENTS_ROOT:-$_PRODUCER_EVENTS_DEFAULT_ROOT}"
PRODUCER_EVENTS_CLI="${PRODUCER_EVENTS_CLI:-$PRODUCER_EVENTS_ROOT/homelab_events/producers/producer_cli.py}"
PRODUCER_EVENTS_PYTHON="${PRODUCER_EVENTS_PYTHON:-/usr/bin/python3}"
PRODUCER_EVENTS_DB="${PRODUCER_EVENTS_DB:-/opt/homelab/data/homelab-events/events.db}"
PRODUCER_EVENTS_SPOOL="${PRODUCER_EVENTS_SPOOL:-/opt/homelab/data/homelab-events/spool}"
PRODUCER_EVENTS_POLICY="${PRODUCER_EVENTS_POLICY:-$PRODUCER_EVENTS_ROOT/config/policy.yaml}"
PRODUCER_EVENTS_REMOTE_URL="${PRODUCER_EVENTS_REMOTE_URL:-https://events.example.duckdns.org/remote-event}"
PRODUCER_EVENTS_REMOTE_SECRET_FILE="${PRODUCER_EVENTS_REMOTE_SECRET_FILE:-/etc/homelab-events/remote.secret}"
PRODUCER_EVENTS_REMOTE_SPOOL="${PRODUCER_EVENTS_REMOTE_SPOOL:-/var/lib/homelab-events/spool}"

_emit_event_cli() {
  local mode="$1"
  local source="$2"
  local host="$3"
  local service="$4"
  local resource="$5"
  local event_name="$6"
  local status="$7"
  local severity="$8"
  local page_policy="$9"
  local message="${10}"
  local dedupe_key="${11:-}"
  shift 11 || true

  local args=(
    --mode "$mode"
    --source "$source"
    --host "$host"
    --service "$service"
    --resource "$resource"
    --event-name "$event_name"
    --status "$status"
    --severity "$severity"
    --page-policy "$page_policy"
    --message "$message"
  )
  if [[ -n "$dedupe_key" ]]; then
    args+=(--dedupe-key "$dedupe_key")
  fi
  if [[ "$mode" == "remote" ]]; then
    args+=(
      --remote-endpoint "$PRODUCER_EVENTS_REMOTE_URL"
      --remote-secret-file "$PRODUCER_EVENTS_REMOTE_SECRET_FILE"
      --remote-spool "$PRODUCER_EVENTS_REMOTE_SPOOL"
    )
  else
    args+=(
      --db "$PRODUCER_EVENTS_DB"
      --spool "$PRODUCER_EVENTS_SPOOL"
      --policy "$PRODUCER_EVENTS_POLICY"
    )
  fi

  # Shadow intake is compatibility-only and must not break its legacy caller.
  # Canonical mode returns a failure when neither the ledger nor its spool can
  # accept the event, so a migrated producer fails closed rather than silently
  # dropping its notification evidence.
  if PYTHONPATH="$PRODUCER_EVENTS_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
      "$PRODUCER_EVENTS_PYTHON" "$PRODUCER_EVENTS_CLI" "${args[@]}" >/dev/null 2>&1; then
    return 0
  fi
  if [[ "$mode" == "canonical" || "$mode" == "remote" ]]; then
    return 75
  fi
  return 0
}

emit_event_local() {
  if [[ "$#" -lt 9 ]]; then
    return 0
  fi
  _emit_event_cli "${PRODUCER_EVENTS_MODE:-${HOMELAB_EVENTS_MODE:-shadow}}" "$@"
}

emit_event_remote() {
  if [[ "$#" -lt 9 ]]; then
    return 0
  fi
  _emit_event_cli remote "$@"
}
