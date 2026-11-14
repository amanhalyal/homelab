#!/usr/bin/env python3
"""Guarded SnapRAID maintenance for Legion's direct ext4 data disks."""
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

from arcadia_verify_mounts import validate_record

ROOT = Path('/var/lib/snapraid')
LOG = Path('/var/log/snapraid')
CONF = '/etc/snapraid.conf'
# Disk identity. Replace the placeholders with each filesystem's UUID (from blkid) and the
# drive's ATA serial (from smartctl -i). Every run checks both before touching a disk.
DISKS = {
    'atlas': ('/mnt/atlas', '<atlas-uuid>', '<atlas-serial>'),
    'aegir': ('/mnt/aegir', '<aegir-uuid>', '<aegir-serial>'),
    'parity': ('/mnt/argus', '<argus-uuid>', '<argus-serial>'),
}
CHILD = None


def read_sync_hold():
    try:
        hold = json.loads((ROOT/'sync-hold.json').read_text())
    except FileNotFoundError:
        return None
    if not isinstance(hold, dict) or not hold.get('id') or not hold.get('created_at'):
        raise ValueError('Invalid sync hold; refusing parity updates')
    return hold


def assert_sync_allowed():
    if read_sync_hold() is not None:
        raise ValueError('Sync is held by the operator; release the hold in Home Assistant first')


def mounts():
    seen = set()
    devices = {}
    for name, (path, uuid, serial) in DISKS.items():
        r = subprocess.run(['findmnt', '-J', '-t', 'ext4', '-M', path,
                            '-o', 'TARGET,UUID,FSTYPE,OPTIONS,SOURCE'],
                           capture_output=True, text=True, check=True, timeout=15)
        records = json.loads(r.stdout)['filesystems']
        validate_record(path, uuid, records)
        dev = os.stat(path).st_dev
        if dev in seen or dev == os.stat('/').st_dev:
            raise ValueError('Disk is duplicated or resolves to the root filesystem')
        seen.add(dev)
        part = records[0]['source']
        parent = subprocess.check_output(['lsblk', '-ndo', 'PKNAME', part], text=True).strip()
        if not parent or '/' in parent:
            raise ValueError('Cannot resolve physical disk: ' + part)
        devices[name] = '/dev/' + parent
    return devices


class TemperatureHigh(ValueError):
    """Healthy but too hot to start or finalize maintenance."""


def health(devices):
    result = {}
    for name, device in devices.items():
        r = subprocess.run(['smartctl', '-j', '-i', '-H', '-A', '-d', 'sat', device],
                           capture_output=True, text=True, timeout=90)
        d = json.loads(r.stdout)
        if r.returncode & 63 or d.get('smart_status', {}).get('passed') is not True:
            raise ValueError(name + ': SMART unavailable or unhealthy')
        if d.get('serial_number') != DISKS[name][2]:
            raise ValueError(name + ': physical serial mismatch')
        attrs = {a['id']: a['raw']['value'] for a in d.get('ata_smart_attributes', {}).get('table', [])}
        if any(attrs.get(a, -1) != 0 for a in (5, 197, 198)):
            raise ValueError(name + ': reallocated, pending or uncorrectable sectors')
        temp = d.get('temperature', {}).get('current')
        if temp is None:
            raise ValueError(name + ': temperature unavailable')
        if temp >= 55:
            raise TemperatureHigh(name + f': temperature {temp}C is at least 55C')
        result[name] = {'temperature': temp, 'crc': attrs.get(199), 'serial': d['serial_number']}
    return result


def cooled_health(devices, attempts=16, interval=120):
    # A clean scrub remains valid while drives cool; never retry missing SMART,
    # bad sectors or an identity mismatch as though they were only heat.
    for attempt in range(attempts):
        try:
            return health(devices)
        except TemperatureHigh:
            if attempt+1==attempts:
                raise
            print('Data check passed; waiting for drives to cool before final health confirmation', flush=True)
            time.sleep(interval)
            devices=mounts()


def parse_files(log):
    files = {}
    for line in log.splitlines():
        if line.startswith('file:'):
            match = re.fullmatch(r'file:([^:]+):(.*):(\d+):(-?\d+):(\d+):(\d+)', line)
            if not match:
                raise ValueError('Unrecognized SnapRAID file-list record')
            disk, path, size, *_ = match.groups()
            files[(disk, path)] = int(size)
    return files


def deletion_gate(log, files):
    counts = dict(re.findall(r'^summary:(equal|added|removed|updated|moved|copied|restored):(\d+)$', log, re.M))
    if len(counts) != 7 or not re.search(r'^summary:exit:(equal|diff)$', log, re.M):
        raise ValueError('Incomplete or unrecognized diff summary')
    counts = {k: int(v) for k, v in counts.items()}
    removed = []
    for line in log.splitlines():
        if line.startswith('scan:remove:'):
            _, _, disk, path = line.split(':', 3)
            if (disk, path) not in files:
                raise ValueError('Removed file absent from saved content inventory')
            removed.append((disk, path))
    if len(removed) != counts['removed']:
        raise ValueError('Removed-file details do not match summary')
    size = sum(files[key] for key in removed)
    if len(removed) >= 100 or size >= 10 * 1024**3:
        raise ValueError('Deletion gate: at least 100 files or 10 GiB removed')
    for disk in {d for d, _ in files}:
        total = sum(1 for d, _ in files if d == disk)
        deleted = sum(1 for d, _ in removed if d == disk)
        if total and deleted / total >= 0.05:
            raise ValueError('Deletion gate: at least 5% removed from ' + disk)
    return {**counts, 'removed_bytes': size}


def event(status, message, name="snapraid_maintenance"):
    # Canonical local ledger, never direct ntfy delivery. Notification failure
    # must not change the result of a completed storage operation.
    try:
        r = subprocess.run([
            '/usr/bin/python3', '/opt/homelab/scripts/homelab-events/producer_cli.py',
            '--mode', 'canonical', '--source', 'snapraid', '--host', 'legion',
            '--service', 'snapraid', '--resource', 'arcadia', '--event-name', name,
            '--status', status, '--severity', 'warning' if status == 'failure' else 'info',
            '--page-policy', 'digest', '--message', message,
            '--dedupe-key', 'snapraid/legion/arcadia/' + name,
            '--policy', '/etc/homelab-events/policy-canary.yaml'],
            capture_output=True, text=True, timeout=30)
        if r.returncode:
            print('Event intake unavailable; maintenance result retained locally', flush=True)
    except Exception as e:
        print('Event intake failed: ' + str(e), flush=True)


def initial_running():
    # A long-running Type=oneshot remains activating until its work completes.
    states = subprocess.check_output(['systemctl', 'show', 'snapraid-job@initial.service',
                                      'snapraid-job@finalize-initial.service',
                                      '--property=ActiveState', '--value'], text=True).splitlines()
    return any(state.strip() in ('active', 'activating', 'reloading') for state in states)


def monitor():
    """Run independently of mount dependencies and the long maintenance lock."""
    receipt = ROOT/'monitor.json'
    previous = json.loads(receipt.read_text()) if receipt.exists() else {}
    try:
        mounts()
        last_sync = ROOT/'last-sync.json'
        active = initial_running()
        if last_sync.exists():
            age = time.time() - json.loads(last_sync.read_text())['time']
            if age > 36 * 3600:
                raise ValueError('Last successful parity sync is more than 36 hours old')
        elif not active:
            raise ValueError('No successful parity sync and initial job is not active')
        if not (ROOT/'initialized.json').exists() and not active:
            raise ValueError('Initial verification has not completed')
        state = {'result': 'success', 'time': time.time()}
        if previous.get('result') == 'failure':
            event('success', 'SnapRAID mounts and freshness checks recovered', 'snapraid_health')
    except Exception as error:
        state = {'result': 'failure', 'time': time.time(), 'error': str(error)}
        event('failure', 'SnapRAID health check failed: ' + str(error), 'snapraid_health')
    receipt.write_text(json.dumps(state, indent=2)+'\n')
    print(json.dumps(state), flush=True)
    return 0 if state['result'] == 'success' else 1


def stop(signum, frame):
    raise KeyboardInterrupt


def rotation_deadline(now=None):
    current=now or datetime.now(ZoneInfo('Asia/Kolkata'))
    boundary=current.replace(hour=1,minute=50,second=0,microsecond=0)
    resume=current.replace(hour=2,minute=15,second=0,microsecond=0)
    if boundary<=current<resume:
        raise ValueError('Immich backup rotation window (01:50 to 02:15 IST)')
    if current>=resume:boundary+=timedelta(days=1)
    return boundary.timestamp()


def main():
    global CHILD
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['initial', 'sync', 'scrub', 'full-scrub', 'status', 'diff', 'monitor'])
    args = parser.parse_args()
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    LOG.mkdir(mode=0o700, exist_ok=True)
    if args.mode == 'monitor':
        return monitor()
    with (ROOT/'maintenance.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another SnapRAID operation is active; skipped', flush=True)
            return 0
        run = LOG/(time.strftime('%Y%m%dT%H%M%S') + '-' + args.mode)
        run.mkdir(mode=0o700)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        step = 0
        deadline = None

        def command(command_args, accepted=(0,)):
            global CHILD
            nonlocal step
            if command_args == ['sync']:
                assert_sync_allowed()
            if deadline is not None and time.time() >= deadline:
                raise ValueError('Stopping before the 02:00 Immich backup rotation')
            step += 1
            tag = command_args[-1]
            log = run/f'{step:02d}-{tag}.log'
            out = run/f'{step:02d}-{tag}.stdout'
            print('Running ' + ' '.join(command_args) + '; log=' + str(log), flush=True)
            with out.open('w') as output:
                CHILD = subprocess.Popen(['snapraid', '-c', CONF, '-l', str(log)] + command_args,
                                         stdout=output, stderr=subprocess.STDOUT)
                while True:
                    try:
                        rc = CHILD.wait(timeout=60)
                        break
                    except subprocess.TimeoutExpired:
                        mounts()
                        if deadline is not None and time.time() >= deadline:
                            raise ValueError("Stopping before the 02:00 Immich backup rotation")
                CHILD = None
            if rc not in accepted:
                raise ValueError('SnapRAID ' + tag + ' failed (exit ' + str(rc) + '); see ' + str(out))
            return log.read_text()

        try:
            devices = mounts()
            baseline = health(devices)
            (run/'health-before.json').write_text(json.dumps(baseline, indent=2)+'\n')
            if args.mode in ('status', 'diff'):
                command([args.mode], (0, 2) if args.mode == 'diff' else (0,))
                return 0
            deadline = rotation_deadline()
            if args.mode != 'initial' and not (ROOT/'initialized.json').exists():
                raise ValueError('Initial sync and verification have not completed')
            if args.mode in ('initial', 'sync'):
                assert_sync_allowed()
                contents = [Path('/var/lib/snapraid/snapraid.content'), Path('/mnt/atlas/.snapraid/content'), Path('/mnt/aegir/.snapraid/content')]
                if args.mode == 'sync' and not all(p.is_file() for p in contents):
                    raise ValueError('A content copy is missing; refusing sync')
                files = parse_files(command(['list'])) if any(p.exists() for p in contents) else {}
                # Refuse disappearance of any previously protected top-level tree.
                for disk, relative in files:
                    root = Path(DISKS[disk][0])/relative.split('/')[0]
                    if not root.exists():
                        raise ValueError('Protected top-level tree missing: ' + str(root))
                diff = command(['diff'], (0, 2))
                gate = deletion_gate(diff, files)
                (run/'deletion-gate.json').write_text(json.dumps(gate, indent=2)+'\n')
                mounts()
                # No --force flags: SnapRAID's own empty/zero/UUID guards stay on.
                command(['sync'])
                command(['status'])
                (ROOT/'last-sync.json').write_text(json.dumps({'time': time.time(), 'log': str(run)})+'\n')
            if args.mode in ('initial', 'full-scrub'):
                command(['-p', 'full', 'scrub'])
            elif args.mode == 'scrub':
                command(['-p', '8', '-o', '10', 'scrub'])
            command(['status'])
            after = cooled_health(mounts())
            (run/'health-after.json').write_text(json.dumps(after, indent=2)+'\n')
            for disk in baseline:
                if after[disk]['crc'] != baseline[disk]['crc']:
                    raise ValueError(disk + ': CRC counter changed during maintenance')
            receipt = {'mode': args.mode, 'completed_at': time.time(), 'log': str(run), 'result': 'success'}
            if args.mode == 'initial':
                (ROOT/'initialized.json').write_text(json.dumps(receipt, indent=2)+'\n')
            (run/'result.json').write_text(json.dumps(receipt, indent=2)+'\n')
            event('success', 'SnapRAID ' + args.mode + ' completed successfully')
            print(json.dumps(receipt), flush=True)
        except BaseException as error:
            if CHILD is not None and CHILD.poll() is None:
                CHILD.send_signal(signal.SIGINT)
                try:
                    CHILD.wait(timeout=150)
                except subprocess.TimeoutExpired:
                    CHILD.kill()
                    CHILD.wait()
            (run/'result.json').write_text(json.dumps({'result': 'failure', 'error': str(error), 'time': time.time()})+'\n')
            event('failure', 'SnapRAID ' + args.mode + ' blocked or failed: ' + str(error))
            raise
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (Exception, KeyboardInterrupt) as error:
        print('SnapRAID maintenance stopped: ' + str(error), file=sys.stderr)
        sys.exit(1)
