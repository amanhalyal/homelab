#!/usr/bin/env python3
# Shared wallboard state and hardware actions. The rotation loop and the Home
# Assistant control API both call this module, so there is one hardware layer.
import json
import os
import re
import subprocess
import time
import glob

import kanbn_client
from paths import CURRENT_PATH, ROOT_DIR, ROTATION_PATH, SCHEDULE_PATH

BACKLIGHT_DIR = "/sys/class/backlight/intel_backlight"

ROTATION_PATH = str(ROTATION_PATH)
SCHEDULE_PATH = str(SCHEDULE_PATH)
CURRENT_PATH = str(CURRENT_PATH)
SCREENSHOT_RAW = "/tmp/heimdall-live.png"
SCREENSHOT_CROPPED = "/tmp/heimdall-live-crop.png"

COLUMNS = ["todo", "doing", "on_hold", "done"]
VALID_PAGES = ["clock", "grafana", "kanban"]

# Nexus power-control scripts. Absolute paths, because shell aliases only
# resolve in an interactive shell and not in a subprocess.
_PC_DIR = ROOT_DIR.parent / "scripts" / "pc-control"
PC_SCRIPTS = {
    "wake": str(_PC_DIR / "wake-nexus.sh"),
    "shutdown": str(_PC_DIR / "shutdown-nexus.sh"),
    "restart": str(_PC_DIR / "restart-nexus.sh"),
    "sleep": str(_PC_DIR / "sleep-nexus.sh"),
    "abort": str(_PC_DIR / "abort-nexus.sh"),
}

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _atomic_write_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)


# ---------- display / brightness / monitor ----------

# Querying brightness and monitor state costs several hundred milliseconds per
# call on this machine, and the control API asks for both on every request.
# Cache both with a short TTL, and have the setters write the now-known value
# straight into the cache instead of re-querying it right after.
_STATE_CACHE = {}
_CACHE_TTL = 8  # seconds, matching the Home Assistant polling interval


def _cache_get(key):
    entry = _STATE_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(key, value):
    _STATE_CACHE[key] = (value, time.time())


def get_current_page():
    try:
        with open(CURRENT_PATH) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None


def set_current_page(page):
    if page not in VALID_PAGES:
        raise ValueError(f"Unknown page: {page}")
    with open(CURRENT_PATH, "w") as f:
        f.write(page)
    return page


# The Beacon (the external wallboard monitor) has no working DDC/CI: its EDID
# reads fine but DDC communication fails, so a real backlight dim is not
# possible. Brightness is therefore software gamma scaling through
# wl-gammarelay-rs on the Wayland session, driven over the user D-Bus.
# Floored at 10% rather than 0 so it can never go fully black with no easy way
# back. Use monitor_set(False) for that instead.
def _session_env():
    runtime = f"/run/user/{os.getuid()}"
    return {
        **os.environ,
        "XDG_RUNTIME_DIR": runtime,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={runtime}/bus",
    }


def _relay_brightness(value=None):
    cmd = ["busctl", "--user"]
    if value is None:
        cmd += ["get-property", "rs.wl-gammarelay", "/outputs/HDMI_A_1", "rs.wl.gammarelay", "Brightness"]
    else:
        cmd += ["set-property", "rs.wl-gammarelay", "/outputs/HDMI_A_1", "rs.wl.gammarelay", "Brightness", "d", str(value)]
    if value is None:
        out = subprocess.run(cmd, check=True, timeout=10, capture_output=True, text=True, env=_session_env()).stdout.strip()
        return float(out.split()[-1])
    subprocess.run(cmd, check=True, timeout=10, capture_output=True, text=True, env=_session_env())
    return float(value)


def get_brightness_pct():
    cached = _cache_get("brightness")
    if cached is not None:
        return cached
    pct = round(_relay_brightness() * 100)
    _cache_set("brightness", pct)
    return pct


def set_brightness_pct(pct):
    pct = max(10, min(100, pct))
    _relay_brightness(pct / 100.0)
    _cache_set("brightness", pct)  # we just set it - no need to re-query to know it
    return pct


def _hyprctl_env():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    sockets = glob.glob(os.path.join(runtime, "hypr", "*", ".socket.sock"))
    if not sockets:
        raise RuntimeError("Hyprland socket not found")
    signature = os.path.basename(os.path.dirname(sockets[0]))
    return {**os.environ, "XDG_RUNTIME_DIR": runtime, "HYPRLAND_INSTANCE_SIGNATURE": signature}


def monitor_set(on):
    action = "enable" if on else "disable"
    lua = f'hl.dsp.dpms({{ action = "{action}", monitor = "HDMI-A-1" }})'
    subprocess.run(
        ["/usr/bin/hyprctl", "dispatch", lua],
        env=_hyprctl_env(), check=True, timeout=10, capture_output=True, text=True,
    )
    _cache_set("monitor_on", on)
    return on


def monitor_status():
    cached = _cache_get("monitor_on")
    if cached is not None:
        return cached
    out = subprocess.run(
        ["/usr/bin/hyprctl", "monitors", "-j"], env=_hyprctl_env(),
        check=True, timeout=10, capture_output=True, text=True,
    ).stdout
    monitors = json.loads(out)
    hdmi = next((m for m in monitors if m.get("name") == "HDMI-A-1"), None)
    if hdmi is None:
        raise RuntimeError("HDMI-A-1 monitor not found")
    on = bool(hdmi.get("dpmsStatus")) and not bool(hdmi.get("disabled"))
    _cache_set("monitor_on", on)
    return on


def capture_heimdall():
    subprocess.run(
        ["/usr/bin/grim", "-o", "HDMI-A-1", SCREENSHOT_CROPPED],
        env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}", "WAYLAND_DISPLAY": "wayland-1"},
        check=True, timeout=15,
    )
    return SCREENSHOT_CROPPED


# ---------- kanban ----------
# Reads and mutations are delegated to the kan.bn adapter.

def load_tasks():
    return kanbn_client.load_tasks()


def add_task(text):
    text = text.strip()
    if not text:
        raise ValueError("Card text required")
    return kanbn_client.add_task(text)


def move_task(col, idx):
    data = kanbn_client.load_tasks()
    if col not in COLUMNS or not (0 <= idx < len(data[col])):
        raise ValueError("Invalid card index")
    return kanbn_client.move_task(col, idx)


def resume_task(col, idx):
    data = kanbn_client.load_tasks()
    if col not in COLUMNS or not (0 <= idx < len(data[col])):
        raise ValueError("Invalid card index")
    return kanbn_client.resume_task(col, idx)


def remove_task(col, idx):
    data = kanbn_client.load_tasks()
    if col not in COLUMNS or not (0 <= idx < len(data[col])):
        raise ValueError("Invalid card index")
    return kanbn_client.remove_task(col, idx)


# ---------- rotation ----------

def load_rotation():
    try:
        with open(ROTATION_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "interval_seconds": 300, "pages": []}


def save_rotation(cfg):
    _atomic_write_json(ROTATION_PATH, cfg)


def enable_rotation(interval_seconds=None):
    cfg = load_rotation()
    if interval_seconds is not None:
        cfg["interval_seconds"] = interval_seconds
    cfg["enabled"] = True
    save_rotation(cfg)
    schedule_disabled = False
    sched = load_schedule()
    if sched.get("enabled"):
        sched["enabled"] = False
        save_schedule(sched)
        schedule_disabled = True
    return cfg, schedule_disabled


def disable_rotation():
    cfg = load_rotation()
    cfg["enabled"] = False
    save_rotation(cfg)
    return cfg


def set_rotation_pages(names):
    bad = [n for n in names if n not in VALID_PAGES]
    if bad or not names:
        raise ValueError(f"Unknown page(s): {', '.join(bad) if bad else '(none given)'}")
    cfg = load_rotation()
    cfg["pages"] = names
    save_rotation(cfg)
    return cfg


# ---------- schedule ----------

def load_schedule():
    try:
        with open(SCHEDULE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"enabled": False, "blocks": []}


def save_schedule(cfg):
    _atomic_write_json(SCHEDULE_PATH, cfg)


def hhmm_to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def active_schedule_block(blocks):
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


def enable_schedule():
    cfg = load_schedule()
    if not cfg.get("blocks"):
        raise ValueError("No blocks set - add one first")
    cfg["enabled"] = True
    save_schedule(cfg)
    rotation_disabled = False
    rot = load_rotation()
    if rot.get("enabled"):
        rot["enabled"] = False
        save_rotation(rot)
        rotation_disabled = True
    return cfg, rotation_disabled


def disable_schedule():
    cfg = load_schedule()
    cfg["enabled"] = False
    save_schedule(cfg)
    return cfg


def set_schedule_block(start, page, brightness=None):
    if not TIME_RE.match(start):
        raise ValueError("Invalid time, expected HH:MM")
    if page not in VALID_PAGES:
        raise ValueError(f"Unknown page: {page}")
    block = {"start": start, "page": page}
    if brightness is not None:
        if not (10 <= brightness <= 100):
            raise ValueError("Brightness must be 10-100")
        block["brightness"] = brightness
    cfg = load_schedule()
    blocks = [b for b in cfg.get("blocks", []) if b["start"] != start]
    blocks.append(block)
    cfg["blocks"] = blocks
    save_schedule(cfg)
    return cfg


def remove_schedule_block(start):
    cfg = load_schedule()
    before = len(cfg.get("blocks", []))
    cfg["blocks"] = [b for b in cfg.get("blocks", []) if b["start"] != start]
    if len(cfg["blocks"]) == before:
        raise ValueError(f"No block starting at {start}")
    save_schedule(cfg)
    return cfg


# ---------- Nexus (power control + Tailscale status) ----------

def run_pc_script(action):
    if action not in PC_SCRIPTS:
        raise ValueError(f"Unknown action: {action}")
    return subprocess.run(
        [PC_SCRIPTS[action]], capture_output=True, text=True, timeout=15, check=True,
    ).stdout.strip()


def nexus_status():
    try:
        out = subprocess.run(
            ["tailscale", "status", "--json"],
            capture_output=True, text=True, timeout=10, check=True,
        ).stdout
        data = json.loads(out)
        for peer in data.get("Peer", {}).values():
            # Match the machine's own tailnet name ("nexus."), not other peers
            # whose names merely start with the same letters.
            if peer.get("DNSName", "").lower().startswith("nexus."):
                return {"online": bool(peer.get("Online")), "last_seen": peer.get("LastSeen")}
    except Exception:
        pass
    return {"online": None, "last_seen": None}


def mutate_task(action, card_id, column=None):
    """Use stable kan.bn IDs when mutating from Home Assistant."""
    return kanbn_client.mutate_card(action, card_id, column)
