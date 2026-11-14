#!/usr/bin/env python3
"""Keep Citadel's WLAN rfkill soft state unblocked."""

import os
import time
from pathlib import Path

DEFAULT_RFKILL_ROOT = "/sys/class/rfkill"


def managed_resources():
    """Document the intentionally narrow scope for tests and operators."""
    return ["wlan rfkill soft state"]


def reconcile(root=DEFAULT_RFKILL_ROOT):
    """Clear soft blocks on WLAN radios; leave hard blocks and other radios alone."""
    changed = []
    for device in sorted(Path(root).glob("rfkill*")):
        try:
            radio_type = (device / "type").read_text().strip()
            soft = (device / "soft").read_text().strip()
            hard = (device / "hard").read_text().strip()
        except OSError:
            continue
        if radio_type != "wlan" or soft != "1" or hard != "0":
            continue
        soft_path = device / "soft"
        try:
            soft_path.write_text("0\n")
        except OSError as exc:
            print(f"ERROR: cannot unblock {device.name}: {exc}", flush=True)
            continue
        changed.append(soft_path)
        print(f"unblocked WLAN soft rfkill: {device.name}", flush=True)
    return changed


def poll_seconds():
    try:
        return max(0.1, float(os.environ.get("CITADEL_RFKILL_POLL_SECONDS", "0.25")))
    except ValueError:
        return 0.25


def main():
    root = os.environ.get("CITADEL_RFKILL_ROOT", DEFAULT_RFKILL_ROOT)
    while True:
        reconcile(root)
        time.sleep(poll_seconds())


if __name__ == "__main__":
    raise SystemExit(main())
