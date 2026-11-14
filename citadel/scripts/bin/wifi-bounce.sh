#!/bin/bash
# Weekly Wi-Fi re-association (prophylaxis against slow extender degradation).
# Brief ~5s network blip on Citadel; runs Mon 05:30 via wifi-bounce.timer.
set -euo pipefail
WIFI_DEVICE="${WIFI_DEVICE:-wlan0}"
nmcli dev disconnect "$WIFI_DEVICE"
sleep 3
nmcli dev connect "$WIFI_DEVICE"
