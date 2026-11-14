#!/bin/bash
# Cancel a pending shutdown or restart on Nexus.
set -euo pipefail

if ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 nexus 'sudo shutdown -c'; then
  echo "Pending shutdown/restart on Nexus cancelled (if one was pending)."
else
  echo "ERROR: could not cancel the pending Nexus shutdown/restart." >&2
  exit 1
fi
