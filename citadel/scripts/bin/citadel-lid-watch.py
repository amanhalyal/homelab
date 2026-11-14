#!/usr/bin/env python3
"""Keep Citadel's built-in panel off while the lid is closed."""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_LID_STATE_PATH = "/proc/acpi/button/lid/LID0/state"
DEFAULT_HELPER_PATH = "/opt/homelab/citadel/scripts/bin/citadel-panel-dpms.py"


def lid_state_path():
    return os.environ.get("CITADEL_LID_STATE_PATH", DEFAULT_LID_STATE_PATH)


def read_lid_state(path=None):
    """Return open/closed for the ACPI state file, or None if unknown."""
    try:
        text = Path(path or lid_state_path()).read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r"(?im)^\s*state\s*:\s*(open|closed)\s*$", text)
    return match.group(1).lower() if match else None


def desired_action(state):
    if state == "closed":
        return "off"
    if state == "open":
        return "on"
    return None


def helper_path():
    return os.environ.get("CITADEL_PANEL_HELPER", DEFAULT_HELPER_PATH)


def apply_lid_state(state, *, runner=subprocess.run, helper_path=None):
    """Apply a known lid state; return False without acting if it is unknown."""
    action = desired_action(state)
    if action is None:
        return False
    helper = helper_path or globals()["helper_path"]()
    runner(
        [sys.executable, helper, action],
        check=True,
        timeout=15,
    )
    return True


def poll_seconds():
    try:
        return max(0.5, float(os.environ.get("CITADEL_LID_POLL_SECONDS", "1")))
    except ValueError:
        return 1.0


def main():
    last_applied = None
    last_unknown = False
    while True:
        state = read_lid_state()
        if state is None:
            if not last_unknown:
                print("ERROR: lid state is unavailable or unknown", flush=True)
            last_unknown = True
            time.sleep(poll_seconds())
            continue

        last_unknown = False
        if state != last_applied:
            try:
                apply_lid_state(state)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"ERROR: failed to apply lid state {state}: {exc}", flush=True)
                time.sleep(poll_seconds())
                continue
            print(f"lid={state}: panel action applied", flush=True)
            last_applied = state
        time.sleep(poll_seconds())


if __name__ == "__main__":
    raise SystemExit(main())
