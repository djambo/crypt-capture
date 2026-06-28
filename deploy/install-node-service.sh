#!/usr/bin/env bash
# Install the crypt-capture Kinect node as a systemd service so it starts on
# boot and restarts on failure (no more VNC-in-and-launch-by-hand).
#
# Run on the JETSON, from the repo root:
#     sudo deploy/install-node-service.sh
#
# Optional flag:
#     --headless   also boot WITHOUT the desktop GUI (multi-user.target) for
#                  more capture headroom. Reversible:
#                      sudo systemctl set-default graphical.target
#
# After installing: edit /etc/default/kinect-node (CENTRAL_HOST, SENSOR_ID),
# then `sudo systemctl start kinect-node` and `journalctl -u kinect-node -f`.
set -euo pipefail

UNIT=kinect-node.service
ENVFILE=/etc/default/kinect-node

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"   # <repo>/deploy
REPO_DIR="$(dirname "$SCRIPT_DIR")"
RUN_USER="${SUDO_USER:-$USER}"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run with sudo:  sudo deploy/install-node-service.sh" >&2
  exit 1
fi

HEADLESS=0
[ "${1:-}" = "--headless" ] && HEADLESS=1

echo "Installing $UNIT  (user=$RUN_USER  repo=$REPO_DIR)"

# Per-device env file: never clobber an existing one on reinstall.
if [ ! -f "$ENVFILE" ]; then
  install -m 644 "$SCRIPT_DIR/kinect-node.default" "$ENVFILE"
  echo "  wrote $ENVFILE  <-- EDIT to set CENTRAL_HOST + SENSOR_ID"
else
  echo "  kept existing $ENVFILE"
fi

# Render the unit with this device's user + repo path, then install it.
sed -e "s|__USER__|$RUN_USER|g" -e "s|__WORKDIR__|$REPO_DIR|g" \
    "$SCRIPT_DIR/$UNIT" > "/etc/systemd/system/$UNIT"

# Keep the Kinect USB from autosuspending (a common cause of "camera won't start
# until I replug it" after a cold boot). Installed as a udev rule so it applies
# at enumeration time, every boot.
install -m 644 "$SCRIPT_DIR/99-azure-kinect-usb.rules" \
    /etc/udev/rules.d/99-azure-kinect-usb.rules
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true
echo "  installed USB no-autosuspend udev rule"

systemctl daemon-reload
systemctl enable "$UNIT"

if [ "$HEADLESS" -eq 1 ]; then
  echo "  disabling the desktop GUI (default target -> multi-user.target)"
  systemctl set-default multi-user.target
fi

cat <<EOF

Done. Next steps:
  sudo nano $ENVFILE          # set CENTRAL_HOST + SENSOR_ID
  sudo systemctl start $UNIT
  journalctl -u $UNIT -f      # watch it stream / reconnect

Manage it:
  sudo systemctl restart $UNIT
  sudo systemctl stop $UNIT
  sudo systemctl disable $UNIT   # stop auto-starting on boot
EOF
[ "$HEADLESS" -eq 1 ] && echo "  sudo reboot                    # to drop the GUI now"
