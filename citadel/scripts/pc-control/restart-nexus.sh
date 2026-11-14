#!/bin/bash
# Restart Nexus with a roughly one-minute cancellable grace period.
set -euo pipefail

ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 nexus 'sudo shutdown -r +1 "Remote restart from Citadel"'
echo "Restart scheduled on Nexus in ~1 minute. Run abort-nexus to cancel."
