#!/usr/bin/env python3
"""Generate stable source manifests and verify media-copy contents read-only."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import time


def fingerprint(info):
    return [info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns]


def inventory(root, trees):
    entries = {}

    def walk(path):
        info = path.lstat()
        relative = str(path.relative_to(root))
        common = {'size': info.st_size, 'fingerprint': fingerprint(info)}
        if stat.S_ISLNK(info.st_mode):
            entries[relative] = {**common, 'type': 'symlink', 'target': os.readlink(path)}
        elif stat.S_ISDIR(info.st_mode):
            entries[relative] = {**common, 'type': 'directory'}
            for child in sorted(path.iterdir()):
                walk(child)
        elif stat.S_ISREG(info.st_mode):
            entries[relative] = {**common, 'type': 'file'}
        else:
            raise ValueError(f'Unsupported media file type: {relative}')

    for tree in trees:
        if tree not in ('tvshows', 'anime'):
            raise ValueError('Only tvshows and anime are allowed')
        path = root / tree
        if not path.is_dir() or path.is_symlink():
            raise ValueError(f'{path} must be a real directory')
        walk(path)
    return entries


def digest(path):
    before = path.stat()
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            value.update(block)
    if fingerprint(before) != fingerprint(path.stat()):
        raise ValueError(f'File changed while hashing: {path}')
    return value.hexdigest()


def build_manifest(root, trees, cache=None):
    entries = inventory(root, trees)
    cache_entries = (cache or {}).get('entries', {})
    total = sum(e['size'] for e in entries.values() if e['type'] == 'file')
    completed = 0
    for relative, entry in entries.items():
        if entry['type'] != 'file':
            continue
        previous = cache_entries.get(relative, {})
        if previous.get('fingerprint') == entry['fingerprint'] and previous.get('sha256'):
            entry['sha256'] = previous['sha256']
        else:
            entry['sha256'] = digest(root / relative)
        completed += entry['size']
        print(json.dumps({'event': 'source_hash', 'path': relative,
                          'completed_bytes': completed, 'total_bytes': total}), flush=True)
    fresh = inventory(root, trees)
    if {p: e['fingerprint'] for p, e in fresh.items()} != {
            p: e['fingerprint'] for p, e in entries.items()}:
        raise ValueError('Source inventory changed during manifest generation')
    return {'created_at_epoch': time.time(), 'source_root': str(root),
            'trees': trees, 'entries': entries, 'total_bytes': total}


def verify(manifest, source, destination, previous=None):
    expected = manifest['entries']
    source_entries = inventory(source, manifest['trees'])
    if {p: e['fingerprint'] for p, e in source_entries.items()} != {
            p: e['fingerprint'] for p, e in expected.items()}:
        raise ValueError('Source changed since manifest generation')
    actual = inventory(destination, manifest['trees'])
    if set(actual) != set(expected):
        raise ValueError(f'Copy inventory differs: missing={len(set(expected)-set(actual))}, '
                         f'extra={len(set(actual)-set(expected))}')
    completed = 0
    files = 0
    cached_files = (previous or {}).get('verified_files', {})
    verified_files = {}
    for relative, entry in expected.items():
        result = actual[relative]
        if entry['type'] != result['type']:
            raise ValueError(f'File type mismatch: {relative}')
        if entry['type'] == 'symlink' and entry['target'] != result['target']:
            raise ValueError(f'Symlink mismatch: {relative}')
        if entry['type'] == 'file':
            cached = cached_files.get(relative, {})
            reusable = (cached.get('fingerprint') == result['fingerprint']
                        and cached.get('sha256') == entry['sha256'])
            actual_digest = entry['sha256'] if reusable else digest(destination / relative)
            if entry['size'] != result['size'] or entry['sha256'] != actual_digest:
                raise ValueError(f'File content mismatch: {relative}')
            verified_files[relative] = {'fingerprint': result['fingerprint'],
                                        'sha256': actual_digest}
            completed += entry['size']
            files += 1
            print(json.dumps({'event': 'destination_verified', 'path': relative,
                              'completed_bytes': completed,
                              'total_bytes': manifest['total_bytes']}), flush=True)
    final_source = inventory(source, manifest['trees'])
    if {p: e['fingerprint'] for p, e in final_source.items()} != {
            p: e['fingerprint'] for p, e in expected.items()}:
        raise ValueError('Source changed during destination verification')
    return {'verified_at_epoch': time.time(), 'files': files,
            'bytes': completed, 'result': 'all content hashes matched',
            'verified_files': verified_files}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['create', 'verify'])
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--destination-root', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--result', type=Path)
    parser.add_argument('--reuse-verified', action='store_true',
                        help='Reuse this transaction\'s hashes only for unchanged destination files')
    args = parser.parse_args()
    if args.mode == 'create':
        cache = json.loads(args.manifest.read_text()) if args.manifest.exists() else None
        data = build_manifest(args.source_root, ['tvshows', 'anime'], cache)
        temporary = args.manifest.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, indent=2) + '\n')
        temporary.replace(args.manifest)
    else:
        if args.destination_root is None or args.result is None:
            parser.error('verify requires --destination-root and --result')
        previous = (json.loads(args.result.read_text())
                    if args.reuse_verified and args.result.exists() else None)
        data = verify(json.loads(args.manifest.read_text()),
                      args.source_root, args.destination_root, previous)
        args.result.write_text(json.dumps(data, indent=2) + '\n')
        print(json.dumps({k: v for k, v in data.items() if k != 'verified_files'}), flush=True)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print(f'Media verification failed: {error}', file=sys.stderr)
        sys.exit(1)
