#!/bin/bash
# Wake Nexus over the dedicated Citadel to Nexus wired link.
# NEXUS_MAC must be set (for example in the systemd unit or the caller's environment).
set -euo pipefail

MAC="${NEXUS_MAC:?set NEXUS_MAC to the Nexus Ethernet MAC address}"
BROADCAST="${NEXUS_BROADCAST:?set NEXUS_BROADCAST to the wired link broadcast address}"

# The direct link is deliberate: Citadel's default route is Wi-Fi.
[[ "$(ip route get "$BROADCAST")" == *"dev eno1"* ]] || {
  echo "ERROR: $BROADCAST is not routed via eno1" >&2
  exit 1
}

/usr/bin/wakeonlan -i "$BROADCAST" "$MAC"
printf 'Magic packet sent to Nexus (%s) via eno1.\n' "$MAC"
