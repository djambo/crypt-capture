#!/bin/sh
# Software "replug" of the Azure Kinect at service start.
#
# On a cold boot the Kinect often comes up in a wedged USB state — present on the
# bus but not in a condition the SDK can start() — and only a *physical* replug
# fixes it. The camera has its own barrel-jack power, so a replug doesn't power-
# cycle it; it just forces the USB link to RE-ENUMERATE. We do exactly that here
# by toggling the device's sysfs `authorized` flag (logical disconnect/reconnect),
# so no hands are needed at boot.
#
# Runs as a root ExecStartPre before the node opens the camera. Non-fatal: always
# exits 0 so a missing camera / odd kernel never blocks the service. Toggle with
# RESET_USB_ON_START in /etc/default/kinect-node.
#
# Azure Kinect DK = USB vendor 045e (Microsoft); the depth/color/hub composite
# uses product ids 097a–097e. We match either the vendor+pid or a "Kinect"
# product string, and reset every match (resetting the internal hub re-enumerates
# the cameras under it).

if [ "${RESET_USB_ON_START:-1}" != "1" ]; then
    echo "reset-kinect: RESET_USB_ON_START=${RESET_USB_ON_START:-1}, skipping"
    exit 0
fi

VID=045e
found=0
for dev in /sys/bus/usb/devices/*; do
    [ -f "$dev/idVendor" ] || continue
    [ "$(cat "$dev/idVendor" 2>/dev/null)" = "$VID" ] || continue
    pid=$(cat "$dev/idProduct" 2>/dev/null)
    prod=$(cat "$dev/product" 2>/dev/null)
    match=0
    case "$pid" in 097a|097b|097c|097d|097e) match=1 ;; esac
    case "$prod" in *[Kk]inect*) match=1 ;; esac
    [ "$match" = "1" ] || continue
    [ -w "$dev/authorized" ] || continue
    echo "reset-kinect: re-enumerating $(basename "$dev") ($VID:$pid $prod)"
    echo 0 > "$dev/authorized" 2>/dev/null || true
    sleep 1
    echo 1 > "$dev/authorized" 2>/dev/null || true
    found=$((found + 1))
done

if [ "$found" -eq 0 ]; then
    echo "reset-kinect: no Azure Kinect matched (continuing)"
else
    sleep 2     # let the device re-enumerate before the SDK opens it
fi
exit 0
