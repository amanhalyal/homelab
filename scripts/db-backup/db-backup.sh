#!/bin/bash
# Nightly Postgres dumps for Immich, Nextcloud and kan.bn. The `backup`
# container archives config and state files, but a live database directory is
# not a consistent backup, so each database is dumped with pg_dump instead.
# Retention matches the `backup` container: keep the newest 3 per database.
set -Eeuo pipefail

. /opt/homelab/scripts/homelab-events/lib/producer_event.sh

DEST="/mnt/atlas/p2_backups/databases"
ENV_FILE="/opt/homelab/compose/.env"
RETENTION=3
ERROR_DIR="/run/db-backup"
LOCK_FILE="$ERROR_DIR/db-backup.lock"

if [ "$(id -u)" -ne 0 ]; then
  printf '%s\n' 'db-backup.sh must run as root.' >&2
  exit 1
fi

# Serialize all Atlas archive publication/retention against parity maintenance.
exec 8>"${SNAPRAID_LOCK:-/var/lib/snapraid/maintenance.lock}"
flock -x 8

if ! mkdir -p "$ERROR_DIR" || ! chmod 755 "$ERROR_DIR"; then
  printf 'Cannot prepare error/lock directory: %s\n' "$ERROR_DIR" >&2
  exit 1
fi
if ! mkdir -p "$DEST"; then
  printf 'Cannot prepare backup directory: %s\n' "$DEST" >&2
  exit 1
fi

# The root cron entry is canonical. Refuse a second invocation before it can
# touch shared timestamped files or remove an in-flight dump.
exec 9>"$LOCK_FILE" || {
  printf 'Cannot open backup lock: %s\n' "$LOCK_FILE" >&2
  exit 1
}
if ! flock -n 9; then
  printf '[%s] SKIP: another database backup is already running.\n' \
    "$(date '+%Y-%m-%d %H:%M:%S')" >&2
  exit 0
fi

record_event() {
  emit_event_local "$@"
}

# The manifest records a bare relative filename, never an absolute path, so the
# checksum verifies from any mount point.
write_manifest() {
  local artifact="$1"
  local dir name manifest tmp
  dir=$(dirname "$artifact")
  name=$(basename "$artifact")
  manifest="${artifact}.sha256"
  tmp="${manifest}.tmp.$$"

  if [ ! -s "$artifact" ]; then
    printf 'Cannot write manifest: missing/empty artifact: %s\n' "$artifact" >&2
    rm -f "$tmp"
    return 1
  fi
  if ! (cd "$dir" && sha256sum "$name" > "$tmp"); then
    rm -f "$tmp"
    return 1
  fi
  if [ ! -s "$tmp" ] || ! mv -f "$tmp" "$manifest"; then
    rm -f "$tmp"
    return 1
  fi
}

TS=$(date +%Y%m%d-%H%M%S)
FAILED=""

dump_pg() {
  local container="$1" pg_user="$2" db="$3" label="$4"
  local out="$DEST/${label}-${TS}.dump"
  local tmp="${out}.partial.$$"

  rm -f "$tmp" "${tmp}.gz"
  if ! docker exec "$container" pg_dump -U "$pg_user" -Fc "$db" \
      > "$tmp" 2>"$ERROR_DIR/db-backup-${label}.err"; then
    FAILED="${FAILED}${label}(pg_dump) "
    rm -f "$tmp" "${tmp}.gz"
    return 0
  fi
  if [ ! -s "$tmp" ]; then
    FAILED="${FAILED}${label}(empty) "
    rm -f "$tmp" "${tmp}.gz"
    return 0
  fi
  if ! gzip -f "$tmp"; then
    FAILED="${FAILED}${label}(gzip) "
    rm -f "$tmp" "${tmp}.gz"
    return 0
  fi
  if [ ! -s "${tmp}.gz" ] || ! gzip -t "${tmp}.gz" >/dev/null 2>&1; then
    FAILED="${FAILED}${label}(gzip-check) "
    rm -f "$tmp" "${tmp}.gz"
    return 0
  fi
  if ! mv -f "${tmp}.gz" "${out}.gz"; then
    FAILED="${FAILED}${label}(publish) "
    rm -f "$tmp" "${tmp}.gz"
    return 0
  fi
  # Publish the manifest last and atomically so the mirror never sees an
  # empty/partial checksum file beside a backup.
  if ! write_manifest "${out}.gz"; then
    FAILED="${FAILED}${label}(manifest) "
    rm -f "${out}.gz" "${out}.gz.sha256"
  fi
}

env_value() { grep -m1 "^$1=" "$ENV_FILE" | cut -d= -f2-; }

dump_pg immich-db "$(env_value DB_USERNAME)" immich_db immich
dump_pg nextcloud-db "$(env_value NEXTCLOUD_DB_USER)" "$(env_value NEXTCLOUD_DB_NAME)" nextcloud
dump_pg kanbn-db kan kan_db kanbn

# Prune to newest N per label (manifest travels with its artifact)
for label in immich nextcloud kanbn; do
  (ls -1t "$DEST"/${label}-*.dump.gz 2>/dev/null || true) | \
    tail -n +$((RETENTION + 1)) | while read -r f; do
      rm -f "$f" "$f.sha256"
    done
done

if [ -n "$FAILED" ]; then
  record_event db-backup legion database-backup /mnt/atlas database_backup_failure failure critical critical "Database backup failed for: $FAILED"
  exit 1
else
  SIZE=$(du -sh "$DEST" | cut -f1)
  record_event db-backup legion database-backup /mnt/atlas routine_success success info digest "Database backups completed ($SIZE total)."
fi
