#!/bin/sh
# Server configuration backup: fail-closed, fixture-testable, one shared path policy.
set -u
POLICY_VERSION=2026-10-09.1
SOURCE_ROOT=${SOURCE_ROOT:-/source}
BACKUP_DIR=${BACKUP_DIR:-/backups/server}
STAGING_DIR=${STAGING_DIR:-/tmp/backups}
RETENTION=${RETENTION:-3}
MIN_SELECTED_BYTES=${MIN_SELECTED_BYTES:-104857600}
MAX_DROP_PERCENT=${MAX_DROP_PERCENT:-60}
SNAPSHOT_MAX_AGE=${SNAPSHOT_MAX_AGE:-90000}
SHA256_CMD=${SHA256_CMD:-sha256sum}
REQUIRED_MEMBERS=${REQUIRED_MEMBERS-"compose/.env server-data/homelab-events/events.db.snapshot server-data/vaultwarden/snapshot/db.sqlite3 server-data/uptime-kuma/kuma.db.snapshot server-data/grafana/data/grafana.db.snapshot server-data/jellyfin/config/data/data/jellyfin.db.snapshot"}
SNAPSHOT_REQUIREMENTS=${SNAPSHOT_REQUIREMENTS-"server-data/homelab-events/events.db.snapshot server-data/vaultwarden/snapshot/db.sqlite3 server-data/uptime-kuma/kuma.db.snapshot server-data/grafana/data/grafana.db.snapshot server-data/jellyfin/config/data/data/jellyfin.db.snapshot"}
log() { printf '[%s] %s\n' "$(date)" "$*"; }
fail() { log "ERROR: $*" >&2; return 1; }
emit() { command -v emit_internal_event >/dev/null 2>&1 && emit_internal_event "$@" || :; }
# Keep this single predicate aligned with write_exclude_file below.
is_excluded() {
  case "$1" in
    server-data/minecraft|server-data/minecraft/*|server-data/prometheus/data|server-data/prometheus/data/*|server-data/victorialogs|server-data/victorialogs/*|\
    */logs|*/logs/*|*/bazarr/cache|*/bazarr/cache/*|*/jellyfin/cache|*/jellyfin/cache/*|*/jellyfin/config/cache|*/jellyfin/config/cache/*|*/jellyseerr/cache|*/jellyseerr/cache/*|*/Cache|*/Cache/*|*/transcodes|*/transcodes/*|data-config/immich-database|data-config/immich-database/*|*/queries.active|*.partial|*/.partial|*/.partial/*) return 0;;
  esac
  return 1
}
write_exclude_file() {
  cat > "$1" <<'EOF'
server-data/minecraft
server-data/minecraft/*
server-data/prometheus/data
server-data/prometheus/data/*

server-data/victorialogs
server-data/victorialogs/*
*/logs
*/logs/*
*/bazarr/cache
*/bazarr/cache/*
*/jellyfin/cache
*/jellyfin/cache/*
*/jellyfin/config/cache
*/jellyfin/config/cache/*
*/jellyseerr/cache
*/jellyseerr/cache/*
*/Cache
*/Cache/*
*/transcodes
*/transcodes/*
data-config/immich-database
data-config/immich-database/*
*/queries.active
*.partial
*/.partial
*/.partial/*
EOF
}
preflight() {
  [ -d "$SOURCE_ROOT" ] || fail "source root missing: $SOURCE_ROOT" || return 1
  for d in server-data data-config compose; do [ -d "$SOURCE_ROOT/$d" ] || fail "required source missing: $d" || return 1; done
  [ -d "$BACKUP_DIR" ] && [ -w "$BACKUP_DIR" ] || fail "backup destination unavailable" || return 1
  [ -d "$STAGING_DIR" ] && [ -w "$STAGING_DIR" ] || fail "staging destination unavailable" || return 1
  if [ "${SKIP_MOUNT_CHECK:-0}" != 1 ]; then
    grep -q " $SOURCE_ROOT/server-data " /proc/self/mountinfo || fail "server-data is not a distinct bind mount" || return 1
    grep -q " $BACKUP_DIR " /proc/self/mountinfo || fail "backup destination is not mounted" || return 1
    if [ -n "${EXPECTED_BACKUP_DEVICE:-}" ]; then
      actual=$(stat -c %d "$BACKUP_DIR") || return 1
      [ "$actual" = "$EXPECTED_BACKUP_DEVICE" ] || fail "backup filesystem ID mismatch" || return 1
    fi
  fi
  now=$(date +%s)
  for rel in $SNAPSHOT_REQUIREMENTS; do
    f="$SOURCE_ROOT/$rel"; [ -s "$f" ] || fail "required snapshot missing/empty: $rel" || return 1
    mt=$(stat -c %Y "$f") || return 1; age=$((now-mt))
    [ "$age" -le "$SNAPSHOT_MAX_AGE" ] || fail "required snapshot stale: $rel (${age}s)" || return 1
  done
}
build_inventory() {
  list=$1; : > "$list"
  for top in server-data data-config compose; do
    find "$SOURCE_ROOT/$top" -type f -print 2>/dev/null
  done | while IFS= read -r f; do
    rel=${f#"$SOURCE_ROOT"/}; is_excluded "$rel" && continue
    size=$(stat -c %s "$f") || exit 1; mt=$(stat -c %Y "$f") || exit 1
    printf '%s\t%s\t%s\n' "$rel" "$size" "$mt"
  done | sort > "$list"
  [ -s "$list" ] || fail "selection is empty" || return 1
}
run_backup() (
  # This subshell releases the lock before the outer loop sleeps overnight.
  exec 8>"${SNAPRAID_LOCK:-/run/snapraid-maintenance.lock}" || return 1
  flock -x 8 || return 1
  preflight || return 1
  work=$(mktemp -d "$STAGING_DIR/.server-backup.XXXXXX") || return 1
  trap 'rm -rf "$work"' EXIT HUP INT TERM
  inv="$work/inventory.tsv"; excl="$work/excludes"; build_inventory "$inv" || return 1; write_exclude_file "$excl"
  selected_bytes=$(awk -F '\t' '{s+=$2} END {printf "%.0f",s}' "$inv"); file_count=$(wc -l < "$inv" | tr -d ' ')
  [ "$selected_bytes" -ge "$MIN_SELECTED_BYTES" ] || fail "selected size collapse: $selected_bytes < $MIN_SELECTED_BYTES" || return 1
  if [ -s "$BACKUP_DIR/.last_selected_bytes" ]; then
    old=$(cat "$BACKUP_DIR/.last_selected_bytes" 2>/dev/null || printf 0)
    if [ "$old" -gt 0 ]; then drop=$(( (old-selected_bytes)*100/old )); [ "$drop" -le "$MAX_DROP_PERCENT" ] || fail "selected size fell ${drop}%" || return 1; fi
  fi
  new_fp=$(sha256sum "$inv" | awk '{print $1}'); old_fp=$(cat "$BACKUP_DIR/.last_fingerprint" 2>/dev/null || :)
  if [ -n "$old_fp" ] && [ "$new_fp" = "$old_fp" ] && [ "${FORCE_BACKUP:-0}" != 1 ]; then log "No changes since last backup, skipping."; rm -rf "$work"; trap - EXIT HUP INT TERM; return 0; fi
  name="backup-$(date +%Y%m%d-%H%M%S).tar.gz"; final="$BACKUP_DIR/$name"
  [ ! -e "$final" ] && [ ! -e "$final.sha256" ] || { fail "archive name collision: $name"; return 1; }
  staged="$work/$name"; start=$(date +%s)
  log "Changes detected; policy=$POLICY_VERSION files=$file_count selected_bytes=$selected_bytes"
  tar -czf "$staged" --exclude-from="$excl" -C "$SOURCE_ROOT" server-data data-config compose
  rc=$?; [ "$rc" -eq 0 ] || [ "$rc" -eq 1 ] || { fail "tar failed (exit $rc), preserving prior good state"; return 1; }
  gzip -t "$staged" || { fail "gzip verification failed"; return 1; }
  listing="$work/listing"; tar -tzf "$staged" > "$listing" || { fail "tar listing failed"; return 1; }
  for rel in $REQUIRED_MEMBERS; do grep -Fqx "$rel" "$listing" || { fail "mandatory archive member missing: $rel"; return 1; }; done
  final="$BACKUP_DIR/$name"; cp "$staged" "$final" || { fail "archive publication failed"; return 1; }
  (cd "$BACKUP_DIR" && $SHA256_CMD "${name}" > "${name}.sha256.tmp") || { rm -f "$final" "$BACKUP_DIR/${name}.sha256.tmp"; fail "manifest publication failed"; return 1; }
  mv "$BACKUP_DIR/${name}.sha256.tmp" "$BACKUP_DIR/${name}.sha256" || { rm -f "$final"; fail "manifest rename failed"; return 1; }
  (cd "$BACKUP_DIR" && sha256sum -c "${name}.sha256") || { rm -f "$final" "$BACKUP_DIR/${name}.sha256"; fail "published checksum failed"; return 1; }
  archive_bytes=$(stat -c %s "$final"); duration=$(( $(date +%s)-start )); ratio=$(awk -v a="$archive_bytes" -v s="$selected_bytes" 'BEGIN{if(s) printf "%.4f",a/s; else print "0"}')
  printf 'policy_version=%s\nselected_bytes=%s\narchive_bytes=%s\ncompression_ratio=%s\nfile_count=%s\nduration_seconds=%s\n' "$POLICY_VERSION" "$selected_bytes" "$archive_bytes" "$ratio" "$file_count" "$duration" > "$final.report"
  printf '%s\n' "$new_fp" > "$BACKUP_DIR/.last_fingerprint.tmp" && mv "$BACKUP_DIR/.last_fingerprint.tmp" "$BACKUP_DIR/.last_fingerprint"
  printf '%s\n' "$selected_bytes" > "$BACKUP_DIR/.last_selected_bytes.tmp" && mv "$BACKUP_DIR/.last_selected_bytes.tmp" "$BACKUP_DIR/.last_selected_bytes"
  ls -1t "$BACKUP_DIR"/backup-*.tar.gz 2>/dev/null | tail -n +$((RETENTION+1)) | while IFS= read -r f; do rm -f "$f" "$f.sha256" "$f.report"; done
  log "Backup complete: $name ($archive_bytes bytes)."
  emit "server-backup-$name" legion backup server_backup_success success info digest server "Server backup completed: $name."
  rm -rf "$work"; trap - EXIT HUP INT TERM
)
if [ -r /usr/local/lib/homelab-events/internal_event.sh ]; then . /usr/local/lib/homelab-events/internal_event.sh; fi
while :; do
  if ! run_backup; then
    emit "server-backup-failure-$(date +%s)" legion backup server_backup_failure failure warning digest server "Server backup failed; prior good state retained."
    : > "$BACKUP_DIR/.failure_open" 2>/dev/null || :
    [ "${RUN_ONCE:-0}" = 1 ] && exit 1
  elif [ -e "$BACKUP_DIR/.failure_open" ]; then
    # A success event has a different dedupe key, so it can never close the failure
    # incident by itself; emit the matching resolved event (same service/host/resource/event_name).
    emit "server-backup-recovered-$(date +%s)" legion backup server_backup_failure resolved info digest server "Server backup recovered after an earlier failure."
    rm -f "$BACKUP_DIR/.failure_open"
  fi
  [ "${RUN_ONCE:-0}" = 1 ] && exit 0
  now=$(date +%s); target=$(date -d "$(date +%Y-%m-%d) 18:00:00" +%s); [ "$target" -gt "$now" ] || target=$((target+86400)); sleep $((target-now))
done
