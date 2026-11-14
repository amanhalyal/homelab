#!/bin/bash
# Shut down Nexus with a roughly one-minute cancellable grace period.
set -euo pipefail

ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 nexus 'sudo shutdown -h +1 "Remote shutdown from Citadel"'
echo "Shutdown scheduled on Nexus in ~1 minute. Run abort-nexus to cancel."
