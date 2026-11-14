#!/bin/sh
# Internal Docker-network event helper. This never contacts ntfy.

_emit_json_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g; s/[[:cntrl:]]//g'
}

emit_internal_event() {
  # event_id host service event_name status severity page_policy resource message
  [ "$#" -ge 9 ] || return 0
  event_id=$1
  host=$2
  service=$3
  event_name=$4
  status=$5
  severity=$6
  page_policy=$7
  resource=$8
  message=$9
  observed_at=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
  dedupe_key="$service/$host/$resource/$event_name"

  event_id=$(_emit_json_escape "$event_id")
  host=$(_emit_json_escape "$host")
  service=$(_emit_json_escape "$service")
  event_name=$(_emit_json_escape "$event_name")
  status=$(_emit_json_escape "$status")
  severity=$(_emit_json_escape "$severity")
  page_policy=$(_emit_json_escape "$page_policy")
  resource=$(_emit_json_escape "$resource")
  message=$(_emit_json_escape "$message")
  dedupe_key=$(_emit_json_escape "$dedupe_key")
  observed_at=$(_emit_json_escape "$observed_at")

  body=$(printf '{"schema_version":1,"event_id":"%s","dedupe_key":"%s","source":"%s","host":"%s","service":"%s","resource":"%s","event":"%s","status":"%s","severity":"%s","page_policy":"%s","observed_at":"%s","message":"%s"}' \
    "$event_id" "$dedupe_key" "$service" "$host" "$service" "$resource" \
    "$event_name" "$status" "$severity" "$page_policy" "$observed_at" "$message")

  # Keep this URL private to the Compose network. If the intake is restarting,
  # retain the already-redacted event for the host replay worker instead of
  # silently dropping backup evidence.
  if wget -q -O /dev/null --header='Content-Type: application/json' \
    --post-data="$body" http://homelab-event-shadow:8088/event >/dev/null 2>&1; then
    return 0
  fi

  spool_dir="${HOMELAB_EVENTS_SPOOL:-/var/lib/homelab-events/spool}"
  if ! mkdir -p "$spool_dir"; then
    return 75
  fi
  spool_name=$(printf '%s' "$event_id" | sha256sum | cut -d' ' -f1).json
  temporary=$(mktemp "$spool_dir/.internal-event.XXXXXX") || return 75
  if ! printf '%s\n' "$body" > "$temporary"; then
    rm -f "$temporary"
    return 75
  fi
  chmod 600 "$temporary"
  if ! mv -n "$temporary" "$spool_dir/$spool_name" 2>/dev/null; then
    # A duplicate spool file is already durable; a conflicting file is left
    # untouched for manual inspection rather than overwritten.
    if [ -e "$spool_dir/$spool_name" ]; then
      rm -f "$temporary"
      return 0
    fi
    rm -f "$temporary"
    return 75
  fi
  return 0
}
