#!/usr/bin/env python3
"""disk-watch: record a canonical event when disk usage crosses threshold.

Checks: Legion SSD, Atlas, Aegir and Arcadia. Dedicated parity fullness is
reported separately by storage telemetry, rather than treated as data capacity.
Silent when OK (watchdog pattern). Cron: hourly.
"""
import os
import json
import time
import sys
import subprocess

sys.path.insert(0, "/opt/homelab/scripts/homelab-events")
from producer_cli import build_event  # noqa: E402
from producer_emit import emit_local_event  # noqa: E402

THRESHOLD = 90.0        # real disks
TMPFS_THRESHOLD = 70.0  # tmpfs is RAM-backed: 70% of 12G = 8.4G of squatted RAM
def usage(mount, host=None):
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes", host,
               "df -P %s | tail -1" % mount]
    else:
        cmd = ["df", "-P", mount]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=20).stdout.split()
        return float(out[4].rstrip("%"))
    except Exception:
        return None


def fstype(mount, host=None):
    """Return filesystem type of mount ('tmpfs', 'ext4', ...) or None."""
    if host:
        cmd = ["ssh", "-o", "BatchMode=yes", host,
               "findmnt -no FSTYPE %s" % mount]
    else:
        cmd = ["findmnt", "-no", "FSTYPE", mount]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=20).stdout.strip()
        return out or None
    except Exception:
        return None


def shadow_event(label, mount, pct, *, tmpfs=False):
    """Record the observation using the configured producer mode."""
    host = "citadel" if label.startswith("Citadel") else "legion"
    event_name = "tmpfs_threshold" if tmpfs else "filesystem_threshold"
    event = build_event(
        source="disk-watch",
        host=host,
        service="disk-watch",
        resource=mount,
        event_name=event_name,
        status="failure",
        severity="critical",
        page_policy="critical",
        message="%s at %.1f%% (%s threshold %.0f%%)" % (
            label,
            pct,
            "tmpfs" if tmpfs else "filesystem",
            TMPFS_THRESHOLD if tmpfs else THRESHOLD,
        ),
    )
    try:
        return emit_local_event(
            event, mode=os.environ.get("HOMELAB_EVENTS_MODE", "shadow")
        )
    except Exception:
        return None


def main():
    checks = [("Legion /", usage("/"))]
    try:
        with open('/var/cache/homelab-storage/status.json') as stream:data=json.load(stream)
        if time.time()-data.get('observed_at',0)<300:
            for name in ('atlas','aegir','arcadia'):
                disk=data['drives'][name]
                if disk.get('present'):
                    usable=disk['used_bytes']+disk['available_bytes']
                    checks.append(('Legion '+disk['mount'],100*disk['used_bytes']/usable))
    except (OSError,ValueError,KeyError):
        pass
    for label, pct in checks:
        if pct is not None and pct >= THRESHOLD:
            shadow_event(label, label.split(" ", 1)[-1], pct)
    # tmpfs squats RAM and doesn't shrink on its own (2026-08-07: backup
    # staging filled /tmp to 94%, pinning Legion at 74% RAM + 8.4G swap).
    # Only meaningful on mounts that are actually tmpfs. Citadel's /tmp is a
    # plain dir on the root disk (79% = migration data, expected), so it must
    # not be held to the RAM threshold (2026-08-31).
    for label, mount, host in [("Legion /tmp (tmpfs)", "/tmp", None),
                               ("Citadel /tmp (tmpfs)", "/tmp", "citadel")]:
        if fstype(mount, host) != "tmpfs":
            continue
        pct = usage(mount, host)
        if pct is not None and pct >= TMPFS_THRESHOLD:
            shadow_event(label, mount, pct, tmpfs=True)
    print("disk-watch: OK")


if __name__ == "__main__":
    main()
