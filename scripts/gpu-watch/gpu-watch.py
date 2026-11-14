#!/usr/bin/env python3
"""gpu-watch: record a canonical event when Legion GPU is unhealthy.

Silent when OK (watchdog pattern). Cron: every 5 min.
"""
import os
import subprocess
import sys

sys.path.insert(0, "/opt/homelab/scripts/homelab-events")
from producer_cli import build_event  # noqa: E402
from producer_emit import emit_local_event  # noqa: E402

TEMP_MAX = 85
VRAM_PCT_MAX = 95.0


def gpu_state():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,temperature.gpu,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20).stdout.strip()
        parts = [p.strip() for p in out.split(",")]
        return {"name": parts[0], "temp": float(parts[1]),
                "mem_pct": float(parts[2]) / float(parts[3]) * 100,
                "util": float(parts[4])}
    except Exception:
        return None


def shadow_event(event_name, message):
    """Record the observation using the configured producer mode."""
    event = build_event(
        source="gpu-watch",
        host="legion",
        service="gpu-watch",
        resource="gpu0",
        event_name=event_name,
        status="failure",
        severity="critical",
        page_policy="critical",
        message=message,
    )
    try:
        return emit_local_event(
            event, mode=os.environ.get("HOMELAB_EVENTS_MODE", "shadow")
        )
    except Exception:
        return None


def main():
    g = gpu_state()
    if not g:
        return  # no GPU / nvidia-smi missing, stay silent
    if g["temp"] >= TEMP_MAX:
        message = "%s at %.0f°C (max %.0f°C), util %.0f%%" % (
            g["name"], g["temp"], TEMP_MAX, g["util"]
        )
        shadow_event("gpu_temperature_high", message)
    if g["mem_pct"] >= VRAM_PCT_MAX:
        message = "%s VRAM at %.0f%%" % (g["name"], g["mem_pct"])
        shadow_event("gpu_vram_high", message)
    print("gpu-watch: OK")


if __name__ == "__main__":
    main()
