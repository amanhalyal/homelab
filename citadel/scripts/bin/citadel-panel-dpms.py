#!/usr/bin/env python3
"""State-aware DPMS control for Citadel's built-in panel.

The unlocked idle and lid paths target eDP-1 only. The existing manual-lock
behavior is retained behind the timeout action: a locked timeout may still
turn both outputs off, while an unlocked timeout never touches HDMI-A-1.
"""

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

PANEL_OUTPUT = "eDP-1"
HYPRCTL = "/usr/bin/hyprctl"
DEFAULT_LID_STATE_PATH = "/proc/acpi/button/lid/LID0/state"


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


def hyprland_env():
    """Build an environment pointing at the active user's Hyprland socket."""
    runtime = os.environ.get("CITADEL_XDG_RUNTIME_DIR") or os.environ.get(
        "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
    )
    signature = os.environ.get("CITADEL_HYPRLAND_INSTANCE_SIGNATURE") or os.environ.get(
        "HYPRLAND_INSTANCE_SIGNATURE"
    )
    if not signature:
        try:
            sockets = sorted(Path(runtime, "hypr").glob("*/.socket.sock"))
        except OSError as exc:
            raise RuntimeError(f"cannot inspect Hyprland sockets: {exc}") from exc
        if not sockets:
            raise RuntimeError("Hyprland socket not found")
        signature = sockets[0].parent.name

    env = dict(os.environ)
    env["XDG_RUNTIME_DIR"] = runtime
    env["HYPRLAND_INSTANCE_SIGNATURE"] = signature
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime}/bus")
    return env


def panel_command(on):
    """Return the only allowed command for panel DPMS state."""
    action = "enable" if on else "disable"
    return [
        HYPRCTL,
        "dispatch",
        f'hl.dsp.dpms({{ action = "{action}", monitor = "{PANEL_OUTPUT}" }})',
    ]


def global_dpms_command(on):
    """Return the legacy manual-lock command, never used for unlocked idle."""
    return [HYPRCTL, "dispatch", "dpms", "on" if on else "off"]


def run_dpms(command, runner=subprocess.run):
    runner(
        command,
        env=hyprland_env(),
        check=True,
        timeout=10,
        capture_output=True,
        text=True,
    )


def graphical_session_locked():
    """Read the active kiosk-user Wayland session's lock hint safely."""
    try:
        rows = subprocess.run(
            ["/usr/bin/loginctl", "list-sessions", "--no-legend"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
        for row in rows:
            parts = row.split()
            if len(parts) < 3 or parts[2] != "kiosk":
                continue
            raw = subprocess.run(
                [
                    "/usr/bin/loginctl",
                    "show-session",
                    parts[0],
                    "-p",
                    "Name",
                    "-p",
                    "Type",
                    "-p",
                    "Class",
                    "-p",
                    "LockedHint",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            props = dict(line.split("=", 1) for line in raw.splitlines() if "=" in line)
            if props.get("Name") == "kiosk" and props.get("Type") == "wayland":
                return props.get("LockedHint") == "yes"
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return False


def handle_action(action, *, lid_path=None, locked=None, runner=subprocess.run):
    """Apply an action and return a small result label for logs/tests."""
    if action == "timeout":
        if locked is None:
            locked = graphical_session_locked()
        if locked:
            run_dpms(global_dpms_command(False), runner=runner)
            return "global-off"
        run_dpms(panel_command(False), runner=runner)
        return "panel-off"

    if action == "off":
        run_dpms(panel_command(False), runner=runner)
        return "panel-off"

    if action in ("on", "resume"):
        state = read_lid_state(lid_path)
        if state is None:
            return "unknown"
        if state == "closed":
            return "unchanged"
        if action == "resume":
            if locked is None:
                locked = graphical_session_locked()
            if locked:
                run_dpms(global_dpms_command(True), runner=runner)
                return "global-on"
        run_dpms(panel_command(True), runner=runner)
        return "panel-on"

    raise ValueError(f"unknown action: {action}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("timeout", "off", "on", "resume"))
    args = parser.parse_args(argv)
    try:
        result = handle_action(args.action)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(result)
    if result == "unknown":
        print("ERROR: lid state is unavailable or unknown", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
