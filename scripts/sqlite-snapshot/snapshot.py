#!/usr/bin/env python3
"""Create atomic, integrity-checked SQLite online-backup snapshots."""
import argparse, os, sqlite3, tempfile
from pathlib import Path

DEFAULT_MAPPINGS = [
    "/opt/homelab/data/uptime-kuma/kuma.db=/opt/homelab/data/uptime-kuma/kuma.db.snapshot",
    "/opt/homelab/data/grafana/data/grafana.db=/opt/homelab/data/grafana/data/grafana.db.snapshot",
    "/opt/homelab/data/jellyfin/config/data/data/jellyfin.db=/opt/homelab/data/jellyfin/config/data/data/jellyfin.db.snapshot",
]

def parse_mappings(values):
    result=[]; destinations=set()
    for value in values:
        if "=" not in value: raise ValueError(f"invalid mapping: {value}")
        src,dst=map(Path,value.split("=",1))
        if dst in destinations: raise ValueError(f"duplicate destination: {dst}")
        destinations.add(dst); result.append((src,dst))
    return result

def snapshot_database(source, destination):
    source=Path(source); destination=Path(destination)
    if not source.is_file(): raise FileNotFoundError(source)
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp_name=tempfile.mkstemp(prefix=f".{destination.name}.",suffix=".tmp",dir=destination.parent)
    tmp=Path(tmp_name)
    try:
        os.fchmod(fd,0o600); os.close(fd)
        src=sqlite3.connect(f"file:{source}?mode=ro",uri=True,timeout=30)
        dst=sqlite3.connect(tmp)
        try:
            src.backup(dst); dst.commit()
            result=dst.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok": raise sqlite3.DatabaseError(f"integrity_check: {result}")
        finally:
            dst.close(); src.close()
        with open(tmp,"rb") as f: os.fsync(f.fileno())
        os.chmod(tmp,0o600); os.replace(tmp,destination)
        dfd=os.open(destination.parent,os.O_RDONLY); os.fsync(dfd); os.close(dfd)
    except Exception:
        try: os.close(fd)
        except OSError: pass
        tmp.unlink(missing_ok=True)
        raise

def main():
    p=argparse.ArgumentParser(); p.add_argument("--map",action="append",dest="mappings")
    args=p.parse_args(); mappings=parse_mappings(args.mappings or DEFAULT_MAPPINGS)
    for src,dst in mappings:
        snapshot_database(src,dst); print(f"snapshot ok: {src} -> {dst}")
    return 0
if __name__ == "__main__": raise SystemExit(main())
