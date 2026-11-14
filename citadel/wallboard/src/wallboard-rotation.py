#!/usr/bin/env python3
# Drives wallboard/var/current from one of two mutually-exclusive sources:
# - schedule.json: time-of-day blocks (takes priority whenever enabled -
#   heimdall_actions.py enforces the mutual exclusion when toggling either on)
# - rotation.json: round-robin through a page list at a fixed interval
# A manual citadel-display/bot page or brightness change still works normally
# whenever neither is enabled - this only ever writes CURRENT/brightness on
# the enabled path, and only on an actual change, so a manual pick made
# between schedule transitions is never stomped on until the next boundary.
import json
import os
import time

from heimdall_actions import set_brightness_pct
from paths import CURRENT_PATH, ROTATION_PATH, SCHEDULE_PATH

BACKLIGHT_DIR = "/sys/class/backlight/intel_backlight"

ROTATION_CONFIG = str(ROTATION_PATH)
SCHEDULE_CONFIG = str(SCHEDULE_PATH)
CURRENT = str(CURRENT_PATH)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def load_rotation():
    return load_json(ROTATION_CONFIG, {"enabled": False, "interval_seconds": 300, "pages": []})


def load_schedule():
    return load_json(SCHEDULE_CONFIG, {"enabled": False, "blocks": []})


def set_current(page):
    with open(CURRENT, "w") as f:
        f.write(page)



def hhmm_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def active_block(blocks):
    if not blocks:
        return None
    ordered = sorted(blocks, key=lambda b: hhmm_to_minutes(b["start"]))
    now_min = hhmm_to_minutes(time.strftime("%H:%M"))
    candidate = ordered[-1]  # wraps from the previous day until the first start is reached
    for b in ordered:
        if hhmm_to_minutes(b["start"]) <= now_min:
            candidate = b
        else:
            break
    return candidate


def main():
    rotation_idx = 0
    last_rotation_switch = 0
    last_schedule_start = None
    was_schedule_enabled = False

    while True:
        schedule = load_schedule()

        if schedule.get("enabled") and schedule.get("blocks"):
            if not was_schedule_enabled:
                last_schedule_start = None  # force a re-apply on enable, even if unchanged
            was_schedule_enabled = True

            block = active_block(schedule["blocks"])
            if block and block["start"] != last_schedule_start:
                set_current(block["page"])
                if "brightness" in block:
                    try:
                        set_brightness_pct(block["brightness"])
                    except Exception as e:
                        print(f"ERROR setting brightness: {e}", flush=True)
                last_schedule_start = block["start"]

        else:
            was_schedule_enabled = False
            last_schedule_start = None

            rotation = load_rotation()
            pages = rotation.get("pages") or []
            if rotation.get("enabled") and pages:
                now = time.time()
                interval = rotation.get("interval_seconds", 300)
                if now - last_rotation_switch >= interval:
                    rotation_idx = (rotation_idx + 1) % len(pages)
                    set_current(pages[rotation_idx])
                    last_rotation_switch = now

        time.sleep(2)


if __name__ == "__main__":
    main()
