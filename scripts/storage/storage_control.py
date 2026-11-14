#!/usr/bin/env python3
"""Authenticated, asynchronous Arcadia review API; no arbitrary host commands."""
import argparse
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

import snapraid_job as raid
from complete_initial_verification import clean_stage, tail

CONTROL = raid.ROOT / 'control'
CONTENTS = (raid.ROOT / 'snapraid.content', Path('/mnt/atlas/.snapraid/content'), Path('/mnt/aegir/.snapraid/content'))
TTL = 3600
ID = re.compile(r'^[a-f0-9]{32}$')


def atomic(path, data):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=True, indent=2) + '\n')
    temp.replace(path)


def baseline():
    hashes = []
    for path in CONTENTS:
        with path.open('rb') as stream:
            hashes.append(hashlib.file_digest(stream, 'sha256').hexdigest())
    if len(set(hashes)) != 1:
        raise ValueError('Content copies disagree; maintenance required')
    return {'content_sha256': hashes[0], 'config_sha256': hashlib.sha256(Path(raid.CONF).read_bytes()).hexdigest()}


def changes(text, files):
    """Validate complete diff details even when its deletion threshold is exceeded."""
    counts = dict(re.findall(r'^summary:(equal|added|removed|updated|moved|copied|restored):(\d+)$', text, re.M))
    if len(counts) != 7 or not re.search(r'^summary:exit:(equal|diff)$', text, re.M):
        raise ValueError('Incomplete diff; no approval can be issued')
    counts = {key: int(value) for key, value in counts.items()}
    removed = {}
    for line in text.splitlines():
        if line.startswith('scan:remove:'):
            _, _, disk, path = line.split(':', 3)
            key = (disk, path)
            if disk not in ('atlas', 'aegir') or key not in files or key in removed:
                raise ValueError('Unknown or duplicate removed-file record')
            if path.startswith('/') or '..' in Path(path).parts:
                raise ValueError('Unsafe saved file path')
            removed[key] = files[key]
    if len(removed) != counts['removed']:
        raise ValueError('Deletion details do not match summary')
    return counts, [{'disk': disk, 'path': path, 'bytes': size}
                    for (disk, path), size in sorted(removed.items())]


def protected_trees(files):
    for disk, relative in files:
        if disk not in ('atlas', 'aegir'):
            raise ValueError('Unknown data disk')
        if not (Path(raid.DISKS[disk][0]) / relative.split('/')[0]).exists():
            raise ValueError('Protected top-level tree missing; approval cannot override this')


def validate_review(review, current_baseline, removed, now=None):
    now = time.time() if now is None else now
    if review.get('consumed_at') or now >= review['expires_at']:
        raise ValueError('Review expired or already used; create a new review')
    if review['baseline'] != current_baseline:
        raise ValueError('Parity baseline changed; create a new review')
    if review['removed_files'] != removed:
        raise ValueError('Deleted files changed; create a new review')


def summarize(review):
    result = {k: v for k, v in review.items() if k not in ('removed_files', 'baseline')}
    result['expired'] = time.time() >= review['expires_at']
    return result


class Runner:
    def __init__(self, directory):
        self.directory = directory
        self.deadline = raid.rotation_deadline()
        self.step = 0

    def command(self, args, allowed=(0,)):
        if time.time() >= self.deadline:
            raise ValueError('Stopping before Immich backup rotation')
        self.step += 1
        log = self.directory / f'{self.step:02d}-{args[-1]}.log'
        with log.with_suffix('.stdout').open('w') as output:
            process = subprocess.Popen(['snapraid', '-c', raid.CONF, '-l', str(log), *args],
                                       stdout=output, stderr=subprocess.STDOUT)
            try:
                while process.poll() is None:
                    try:
                        process.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        raid.mounts()
                        if time.time() >= self.deadline:
                            raise ValueError('Stopping before Immich backup rotation')
                if process.returncode not in allowed:
                    raise ValueError(f'SnapRAID {args[-1]} failed; see {log}')
            except BaseException:
                if process.poll() is None:
                    process.send_signal(signal.SIGINT)
                    try:
                        process.wait(timeout=150)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                raise
        return log


class Controller:
    def __init__(self, root=CONTROL):
        self.root = root
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        (self.root / 'reviews').mkdir(mode=0o700, exist_ok=True)
        self.mutex = threading.Lock()
        self.active = False
        self.job = self.read('job.json', {})
        if self.job.get('state') == 'running':
            self.job.update(state='interrupted', error='Service restarted; create a fresh review')
            atomic(self.root / 'job.json', self.job)

    def read(self, name, default=None):
        try:
            return json.loads((self.root / name).read_text())
        except FileNotFoundError:
            return default

    def review(self, review_id):
        if not isinstance(review_id, str) or not ID.fullmatch(review_id):
            raise ValueError('Invalid review ID')
        return self.read('reviews/' + review_id + '.json') or self.missing()

    @staticmethod
    def missing():
        raise ValueError('Review not found')

    def state(self):
        with self.mutex:
            result = {'job': dict(self.job), 'hold': raid.read_sync_hold()}
            latest = self.read('latest.json')
            result['review'] = summarize(self.review(latest['id'])) if latest else None
        try:
            result['storage'] = json.loads(Path('/var/cache/homelab-storage/status.json').read_text())
        except (OSError, ValueError):
            result['storage'] = {}
        return result

    def hold_action(self, action, review_id=None, hold_id=None):
        # The same lock as every sync closes the hold/start race. Never claim
        # an already-running sync was stopped by placing a marker afterwards.
        with self.mutex, (raid.ROOT / 'maintenance.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Maintenance is running; wait for it to finish before changing the hold') from error
            current = raid.read_sync_hold()
            if action == 'hold':
                if current:
                    return {'hold': current}
                if review_id:
                    self.review(review_id)  # May be expired; holding must still be possible.
                current = {'id': uuid.uuid4().hex, 'created_at': time.time(), 'review_id': review_id}
                atomic(raid.ROOT / 'sync-hold.json', current)
            else:
                if not current or current['id'] != hold_id:
                    raise ValueError('Hold changed; refresh before releasing it')
                (raid.ROOT / 'sync-hold.json').unlink()
                current = None
            return {'hold': current}

    def start(self, mode, review_id=None):
        with self.mutex:
            if self.active:
                raise ValueError('A storage operation is already running')
            if mode == 'approve':
                raid.assert_sync_allowed()
                review = self.review(review_id)
                validate_review(review, review['baseline'], review['removed_files'])
            self.job = {'id': uuid.uuid4().hex, 'mode': mode, 'state': 'running', 'phase': 'Queued', 'started_at': time.time()}
            atomic(self.root / 'job.json', self.job)
            self.active = True
            threading.Thread(target=self.worker, args=(mode, review_id), daemon=False).start()
            return dict(self.job)

    def update(self, **kwargs):
        with self.mutex:
            self.job.update(kwargs)
            atomic(self.root / 'job.json', self.job)

    def worker(self, mode, review_id):
        try:
            with (raid.ROOT / 'maintenance.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                if mode == 'approve':
                    raid.assert_sync_allowed()
                if json.loads((raid.ROOT / 'initialized.json').read_text()).get('result') != 'success':
                    raise ValueError('Initial parity verification is incomplete')
                directory = raid.LOG / (time.strftime('%Y%m%dT%H%M%S') + '-control-' + self.job['id'][:8])
                directory.mkdir(mode=0o700)
                runner = Runner(directory)
                before = raid.health(raid.mounts())
                initial = baseline()
                self.update(phase='Reading saved inventory')
                files = raid.parse_files(runner.command(['list']).read_text())
                protected_trees(files)
                self.update(phase='Scanning current files; this can take several minutes')
                diff = runner.command(['diff'], (0, 2)).read_text()
                counts, removed = changes(diff, files)
                if baseline() != initial:
                    raise ValueError('Parity baseline changed while scanning')
                if mode == 'review':
                    folders = {}
                    for item in removed:
                        key = item['disk'] + '/' + str(Path(item['path']).parent)
                        entry = folders.setdefault(key, {'files': 0, 'bytes': 0})
                        entry['files'] += 1
                        entry['bytes'] += item['bytes']
                    reason = None
                    try:
                        raid.deletion_gate(diff, files)
                    except ValueError as error:
                        reason = str(error)
                    now = time.time()
                    review = {'id': uuid.uuid4().hex, 'created_at': now, 'expires_at': now + TTL,
                              'baseline': initial, 'removed_files': removed, 'counts': counts,
                              'removed_bytes': sum(item['bytes'] for item in removed),
                              'folders': folders, 'guard_reason': reason, 'log': str(directory)}
                    with self.mutex:
                        atomic(self.root / 'reviews' / (review['id'] + '.json'), review)
                        atomic(self.root / 'latest.json', {'id': review['id']})
                    self.update(state='success', phase='Review ready', review_id=review['id'])
                    return
                review = self.review(review_id)
                validate_review(review, initial, removed)
                # One use, including a failed/aborted attempt. Never keep a reusable bypass.
                review['consumed_at'] = time.time()
                atomic(self.root / 'reviews' / (review_id + '.json'), review)
                atomic(directory / 'approved-review.json', review)
                raid.mounts()
                raid.health(raid.mounts())
                protected_trees(files)
                self.update(phase='Syncing reviewed changes')
                raid.assert_sync_allowed()
                clean_stage(runner.command(['sync']))
                atomic(raid.ROOT / 'last-sync.json', {'time': time.time(), 'log': str(directory)})
                self.update(phase='Verifying all newly synced blocks')
                clean_stage(runner.command(['-p', 'new', 'scrub']))
                status = tail(runner.command(['status']))
                if not all(value in status for value in ('summary:has_unsynced:0\n', 'summary:has_unscrubbed:0\n', 'summary:has_bad:0:0:0\n')):
                    raise ValueError('Parity status is not fully verified')
                after = raid.cooled_health(raid.mounts())
                if any(before[d]['crc'] != after[d]['crc'] for d in before):
                    raise ValueError('CRC counter changed during maintenance')
                self.update(phase='Checking for changes made during sync')
                final = runner.command(['diff'], (0, 2)).read_text()
                if 'summary:exit:equal\n' not in final:
                    raise ValueError('Files changed during maintenance; review again')
                atomic(directory / 'result.json', {'mode': 'approved-sync', 'result': 'success', 'completed_at': time.time(), 'review_id': review_id})
                raid.event('success', 'Reviewed deletion batch synced; newly changed blocks verified')
                self.update(state='success', phase='Sync and verification complete')
        except BaseException as error:
            if 'directory' in locals():
                atomic(directory / 'result.json', {'result': 'failure', 'time': time.time(), 'error': str(error)})
            self.update(state='failure', phase='Stopped safely', error=str(error))
            raid.event('failure', 'Storage ' + mode + ' stopped: ' + str(error))
        finally:
            self.update(completed_at=time.time())
            with self.mutex:
                self.active = False


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Do not log credentials or deletion filenames.

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self):
        if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.token):
            return self.reply(401, {'error': 'Unauthorized'})
        url = urlsplit(self.path)
        try:
            if self.command == 'GET' and url.path == '/state':
                return self.reply(200, self.server.control.state())
            if self.command == 'GET' and url.path == '/review':
                query = parse_qs(url.query)
                review = self.server.control.review(query.get('id', [''])[0])
                offset = max(0, int(query.get('offset', ['0'])[0]))
                return self.reply(200, {'review': summarize(review), 'files': review['removed_files'][offset:offset+100], 'offset': offset})
            if self.command == 'POST' and url.path in ('/review', '/approve', '/hold', '/resume'):
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= 1024:
                    raise ValueError('Invalid request length')
                data = json.loads(self.rfile.read(length) or b'{}')
                mode = url.path[1:]
                allowed = {'review': (set(),), 'approve': ({'review_id'},),
                           'hold': (set(), {'review_id'}), 'resume': ({'hold_id'},)}[mode]
                if not isinstance(data, dict) or set(data) not in allowed:
                    raise ValueError('Unexpected command arguments')
                if mode in ('hold', 'resume'):
                    return self.reply(200, self.server.control.hold_action(mode, **data))
                return self.reply(200, self.server.control.start(mode, data.get('review_id')))
            return self.reply(404, {'error': 'Unknown endpoint'})
        except (ValueError, TypeError, KeyError) as error:
            return self.reply(409, {'error': str(error)})

    do_GET = dispatch
    do_POST = dispatch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind', default='172.18.0.1')
    parser.add_argument('--port', type=int, default=8767)
    parser.add_argument('--token-file', default='/etc/arcadia-control.token')
    args = parser.parse_args()
    os.umask(0o077)
    token = Path(args.token_file).read_text().strip()
    if len(token) < 32:
        raise ValueError('Control token must contain at least 32 characters')
    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    server.token = token
    server.control = Controller()
    server.serve_forever()


if __name__ == '__main__':
    main()
