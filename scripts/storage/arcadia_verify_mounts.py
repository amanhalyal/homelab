#!/usr/bin/env python3
"""Refuse to start Arcadia without its two expected writable filesystems."""
import json
import os
import subprocess
import sys

# Replace with the real filesystem UUIDs. Keep in step with DISKS in snapraid_job.py.
BRANCHES = {
    '/mnt/atlas': '<atlas-uuid>',
    '/mnt/aegir': '<aegir-uuid>',
}


def validate_record(path, expected_uuid, filesystems):
    if len(filesystems) != 1:
        raise ValueError(f'{path}: expected one real ext4 mount')
    record = filesystems[0]
    if record.get('target') != path or record.get('fstype') != 'ext4':
        raise ValueError(f'{path}: backing path is not the expected ext4 mount')
    if record.get('uuid') != expected_uuid:
        raise ValueError(f'{path}: filesystem UUID does not match')
    if 'rw' not in (record.get('options') or '').split(','):
        raise ValueError(f'{path}: backing filesystem is not writable')


def main():
    devices = set()
    for path, expected_uuid in BRANCHES.items():
        result = subprocess.run(
            ['findmnt', '--json', '--types', 'ext4', '--mountpoint', path,
             '--output', 'TARGET,UUID,FSTYPE,OPTIONS'],
            capture_output=True, text=True, timeout=10)
        if result.returncode:
            raise ValueError(f'{path}: real backing mount is unavailable')
        validate_record(path, expected_uuid,
                        json.loads(result.stdout).get('filesystems', []))
        device = os.stat(path).st_dev
        if device in devices:
            raise ValueError('Arcadia branches resolve to the same filesystem')
        devices.add(device)
        print(f'Verified {path}: {expected_uuid}, ext4, writable', flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(f'Arcadia mount refused: {error}', file=sys.stderr)
        sys.exit(1)
