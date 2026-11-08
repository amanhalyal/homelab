# 0017. Wallboard display control: brightness, handheld mode, and panel and lid policy

Date: 2026-08-22 to 2026-10-14 · Status: accepted

Citadel drives two displays: the external wallboard monitor (the Beacon) over HDMI, and its own laptop panel. This record covers how each is controlled and the problems met on the way.

## Brightness

**The Beacon has no usable hardware brightness control.** DDC/CI was checked with `ddcutil`: the display data reads fine but the control channel fails. Brightness is therefore a software gamma adjustment, and a single shared module sets it for the control bot, the dashboard backend and the schedule worker, so manual and scheduled changes use one implementation.

**The laptop panel has a real backlight,** so gamma is pinned at 1.0 and the value is written to the kernel backlight, scaled against its maximum. Gamma alone can only dim, not brighten, an already-dim panel. The backlight file was root-owned, so a udev rule gives the `video` group write access and the service user joins that group.

**A stale-process gotcha.** The shared module is imported by three long-running services. After editing it, only two were restarted. The third kept running the old HDMI-only code from memory, and every call from the dashboard returned a clean `200 OK` while never touching the panel. It was found by comparing the backlight file's modification time with the journal timestamps of the requests. Any edit to the shared module now restarts all three services.

**Moving to Wayland broke the method.** After the move to Hyprland, `xrandr` no longer exists and the connector is named `HDMI-A-1`. Hyprland's own brightness state changed without visibly dimming this monitor. The replacement is `wl-gammarelay-rs`, run as a user service and driven over DBus through a per-output brightness property. The old `xrandr` path must not be reintroduced on this session.

## Handheld mode without busy-polling

When the HDMI cable is unplugged, a controller switches the wallboard services off. In that state it launched Python every three seconds to scan all of `/proc` for stale kiosk processes, which cost about 53 seconds of CPU in 32 minutes while the controller itself was idle.

The services already stop cleanly under systemd with `KillMode=control-group`, so continuous hunting duplicated the service manager. The decision was to keep the marker-based cleanup as a fallback that runs once on entering handheld mode, drop the steady-state scan, and poll the cheap DRM connector status every 10 seconds. The steady state is now one sysfs read and one sleep per interval. The trade-off is that docking and undocking can take up to 10 seconds to be noticed.

## Panel and lid policy

The wanted behavior is to keep Citadel logged in with the Beacon workflow running, while switching off only the laptop's own panel.

| State | Laptop panel | Beacon | Wallboard | Session |
|---|---|---|---|---|
| Open and active | On | On | Running | Unlocked |
| 15 minutes idle | Off | On | Running | Unlocked |
| Lid closed | Off | On | Running while docked | Unlocked |
| Lid opened | On | On | Running while docked | Unlocked |

Decisions:

- Use Hyprland's per-output power-management dispatch for the built-in panel only. The idle and lid paths never use global DPMS, brightness-to-zero, suspend, lock, or service actions.
- Keep the existing 900-second idle timeout, which now calls a lock-aware helper. A locked timeout keeps the legacy behavior, and an unlocked one affects the panel only.
- A separate user service watches the ACPI lid state, accepting only the exact values `open` and `closed`. Missing or malformed state leaves the display unchanged and is logged, so it fails safe.
- `systemd-logind` ignores lid actions, because suspending an always-on node would be wrong.
- Lid state is deliberately not wired into the docked or handheld controller. Physical lid position and HDMI presence are different questions.

**Evidence.** The unit tests cover parsing, malformed state, the unlocked and locked timeout paths, and command isolation, and 16 tests pass. In a live run, the panel went off and the Beacon, kiosk process, services, session and boot ID all stayed unchanged, and a capture of the Beacon was intact. A simulated lid fixture produced the sequence on, off, on, and a malformed value caused no action. A physical lid close and open cycle needs a person at the laptop and was recorded as the remaining acceptance step.

## Wi-Fi follow-up

Journal evidence showed the firmware setting a WLAN soft block when the lid closed, independently of logind, DPMS and NetworkManager. A small root-scoped service now reconciles only the WLAN soft-block state, leaving hard blocks and other radios alone. The Beacon is outside its scope.

## Consequences

- Panel power saving covers the panel only. CPU, Docker, network and Beacon power use continue by design.
- Reconnecting HDMI does not force the panel on if it was already blanked. Lid-open or activity does.
- Rollback copies of the prior idle configuration were made before deployment, and each piece can be removed on its own.
