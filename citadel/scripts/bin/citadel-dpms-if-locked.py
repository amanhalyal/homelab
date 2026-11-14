#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path


def graphical_session_locked():
    try:
        rows = subprocess.check_output(
            ["loginctl", "list-sessions", "--no-legend"],
            text=True, timeout=5,
        ).splitlines()
        for row in rows:
            parts = row.split()
            if len(parts) < 3 or parts[2] != "kiosk":
                continue
            sid = parts[0]
            raw = subprocess.check_output(
                ["loginctl", "show-session", sid,
                 "-p", "Name", "-p", "Type", "-p", "Class", "-p", "LockedHint"],
                text=True, timeout=5,
            )
            props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            if props.get("Name") == "kiosk" and props.get("Type") == "wayland":
                return props.get("LockedHint") == "yes"
    except (subprocess.SubprocessError, OSError, ValueError):
        return False
    return False


if graphical_session_locked():
    env = {**os.environ, "XDG_RUNTIME_DIR": "/run/user/1000"}
    try:
        env["HYPRLAND_INSTANCE_SIGNATURE"] = next(
            Path("/run/user/1000/hypr").iterdir()
        ).name
    except (StopIteration, OSError):
        raise SystemExit("Hyprland instance not found")
    subprocess.run(["hyprctl", "dispatch", "dpms", "off"], env=env,
                   check=False, timeout=10)
