"""Read-only freshness status for the backup layers the daily digest reports.

Same rules and the same 30 h freshness window as scripts/daily-digest/daily-digest.py,
published through the storage observer so Home Assistant can show them. Only file
metadata, tiny error markers and a bounded container-log tail are read, never backup contents.
"""
import glob
import os
import subprocess
import time

FRESH_HOURS = 30  # layers run nightly; allow slack for late or retried runs
BACKUP_ROOT = '/mnt/atlas/p2_backups'
LATCH_GLOBS = ('/media/LATCH/vault', '/media/*/LATCH/vault')
DB_PATTERNS = (('immich', 'immich-*.dump.gz'), ('nextcloud', 'nextcloud-*.dump.gz'), ('kanbn', 'kanbn-*.dump.gz'))
DB_ERROR_GLOB = '/run/db-backup/db-backup-*.err'
TAR_LAYERS = (('server', 'backup', 'server', 'backup-*.tar.gz'),
              ('minecraft', 'minecraft-backup', 'minecraft', 'minecraft-backup-*.tar.gz'))
STATUS_CODE = {'FAILED': 0, 'PASS': 1, 'SKIPPED': 2, 'PAUSED': 3}


def newest_artifact(directory, pattern):
    """(basename, size_bytes, mtime) of the newest file matching pattern, or None."""
    files = glob.glob(os.path.join(directory, pattern))
    if not files:
        return None
    path = max(files, key=os.path.getmtime)
    stat = os.stat(path)
    return os.path.basename(path), stat.st_size, stat.st_mtime


def parse_container_log(text):
    """True: latest result was a completed backup. 'unchanged': skipped on purpose. False: explicit error. None: unknown."""
    positions = {'complete': text.rfind('Backup complete.'), 'unchanged': text.rfind('No changes since last backup, skipping.'),
                 'error': max(text.rfind('ERROR: gzip -t failed'), text.rfind('ERROR: tar failed'))}
    kind, position = max(positions.items(), key=lambda item: item[1])
    if position == -1:
        return None
    return 'unchanged' if kind == 'unchanged' else kind == 'complete'


def docker_log_result(container):
    try:
        text = subprocess.run(['docker', 'logs', '--tail', '40', container], capture_output=True, text=True, timeout=15)
        return parse_container_log(text.stdout + text.stderr)
    except (OSError, subprocess.SubprocessError):
        return None


def layer(name, status, detail, artifact=None, now=None, **extra):
    item = {'layer': name, 'status': status, 'detail': detail, **extra}
    if artifact:
        item.update({'newest': artifact[0], 'size_bytes': artifact[1], 'newest_timestamp': artifact[2]})
        if now:
            item['age_hours'] = round((now - artifact[2]) / 3600, 1)
    return item


def tar_layer(name, container, directory, pattern, paused, log_result, now):
    info = newest_artifact(directory, pattern)
    if info is None:
        return layer(name, 'FAILED', 'no artifact found in ' + directory, now=now)
    fresh = (now - info[2]) / 3600 < FRESH_HOURS
    if paused.get(container):
        return layer(name, 'PAUSED', 'paused for parity verification', info, now)
    result = log_result(container)
    if result == 'unchanged':
        return layer(name, 'SKIPPED', 'unchanged since last backup', info, now)
    if not fresh or result is False:
        return layer(name, 'FAILED', 'latest backup is stale' if not fresh else 'latest run reported an error', info, now)
    return layer(name, 'PASS', 'latest backup is fresh', info, now)


def database_layer(directory, now, error_glob):
    mtimes, missing = [], []
    for name, pattern in DB_PATTERNS:
        info = newest_artifact(directory, pattern)
        if info is None:
            missing.append(name)
        else:
            mtimes.append(info[2])
    errors = any(os.path.getsize(path) > 0 for path in glob.glob(error_glob))
    oldest = min(mtimes) if mtimes else None
    fresh = oldest is not None and (now - oldest) / 3600 < FRESH_HOURS
    failed = bool(missing) or errors or not fresh
    detail = ('missing: ' + ', '.join(missing)) if missing else 'error marker present' if errors else 'oldest dump is stale' if not fresh else 'all dumps fresh'
    item = {'layer': 'databases', 'status': 'FAILED' if failed else 'PASS', 'detail': detail}
    if oldest is not None:
        item.update({'newest_timestamp': oldest, 'age_hours': round((now - oldest) / 3600, 1)})  # oldest of the newest dumps
    return item


def latch_layer(now, globs):
    info, mounted = None, False
    for pattern in globs:
        for vault_dir in glob.glob(pattern):
            mounted = True
            info = info or newest_artifact(vault_dir, 'vault-*.age')
    if info is None:
        return layer('vault_latch', 'SKIPPED', 'pendrive not mounted' if not mounted else 'mounted but no copy found', now=now, mounted=mounted)
    fresh = (now - info[2]) / 3600 < FRESH_HOURS
    return layer('vault_latch', 'PASS' if fresh else 'FAILED', 'latest copy is fresh' if fresh else 'latest copy is stale', info, now, mounted=True)


def collect(paused=None, now=None, root=BACKUP_ROOT, log_result=docker_log_result, latch_globs=LATCH_GLOBS, error_glob=DB_ERROR_GLOB):
    """List of layer dicts. Never raises: a broken layer is reported FAILED with the reason."""
    now = now or time.time()
    paused = paused or {}
    layers = []
    builders = [(name, lambda n=name, c=container, d=directory, p=pattern: tar_layer(n, c, os.path.join(root, d), p, paused, log_result, now))
                for name, container, directory, pattern in TAR_LAYERS]
    builders += [('databases', lambda: database_layer(os.path.join(root, 'databases'), now, error_glob)), ('vault_latch', lambda: latch_layer(now, latch_globs))]
    for name, build in builders:
        try:
            layers.append(build())
        except Exception as error:  # observation must never break the storage snapshot
            layers.append({'layer': name, 'status': 'FAILED', 'detail': 'could not be read: ' + type(error).__name__})
    return layers
