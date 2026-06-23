#!/usr/bin/env bash
# deploy-oc-vm-full.sh — ship the templates + provisioning script to a VM and run it.
# This is the remote driver for provision-oc-vm.sh (which must run ON the VM).
#
# Usage:  bash deploy-oc-vm-full.sh [user@host]
#   default host: root@100.124.164.84
set -euo pipefail
VM="${1:-root@100.124.164.84}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST=/opt/oc-provision

echo "==> 1/3 staging dir on $VM"
ssh "$VM" "mkdir -p $DEST"

echo "==> 2/3 shipping profile_templates + scripts + provision-oc-vm.sh"
if command -v rsync >/dev/null 2>&1; then
  rsync -a --delete "$HERE/profile_templates" "$HERE/scripts" "$HERE/provision-oc-vm.sh" "$VM:$DEST/"
else
  tar -C "$HERE" -czf - profile_templates scripts provision-oc-vm.sh | ssh "$VM" "tar -C $DEST -xzf -"
fi

echo "==> 3/3 running provisioning on the VM (brain key falls back to the VM's config)"
ssh "$VM" "cd $DEST && bash provision-oc-vm.sh"
echo "==> full VM provisioning complete."
