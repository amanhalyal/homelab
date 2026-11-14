#!/bin/bash
# Suspend Nexus immediately.
set -euo pipefail

ssh -o BatchMode=yes -o ConnectTimeout=15 -o ServerAliveInterval=5 -o ServerAliveCountMax=2 nexus 'sudo systemctl suspend'
echo "Sleep command sent to Nexus."
