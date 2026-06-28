#!/usr/bin/env bash
# Best-effort "pull latest code before running" step for the kinect-node service.
#
# Run as an ExecStartPre on every (re)start: fetch the configured branch and hard
# -reset the working tree to it, so a boot/reboot lands the Jetson on exactly
# what's pushed to the remote. It is intentionally NON-FATAL — if the network or
# remote is unreachable, it logs and exits 0 so the service still launches the
# code already on disk (offline-resilient). Run it as the user that OWNS the
# clone (the systemd unit's User=), so git uses that user's credentials.
#
# Config comes from /etc/default/kinect-node (exported into the service env):
#   AUTO_UPDATE    1 = update on start (default), 0 = skip
#   UPDATE_BRANCH  branch to track (default: main)
#
# NOTE: this updates *code* only. The systemd unit + /etc/default/kinect-node
# are NOT in the working tree's deployed location, so changes to those still need
# a re-run of deploy/install-node-service.sh.
set -uo pipefail

# Don't let git block on a credential prompt in a non-interactive service —
# fail fast and run the on-disk code instead.
export GIT_TERMINAL_PROMPT=0

# Repo root = parent of this script's deploy/ dir.
REPO_DIR="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
cd "$REPO_DIR" || { echo "update-node: cannot cd to $REPO_DIR, skipping"; exit 0; }

if [ "${AUTO_UPDATE:-1}" != "1" ]; then
  echo "update-node: AUTO_UPDATE=${AUTO_UPDATE:-1}, skipping update"
  exit 0
fi
if ! command -v git >/dev/null 2>&1; then
  echo "update-node: git not found, running on-disk code"
  exit 0
fi

BRANCH="${UPDATE_BRANCH:-main}"
echo "update-node: fetching origin/$BRANCH in $REPO_DIR ..."
if ! git fetch --quiet origin "$BRANCH"; then
  echo "update-node: fetch failed (offline / no access?), running on-disk code"
  exit 0
fi
if ! git reset --hard "origin/$BRANCH"; then
  echo "update-node: reset failed, running on-disk code"
  exit 0
fi
echo "update-node: now at $(git rev-parse --short HEAD) (origin/$BRANCH)"
exit 0
