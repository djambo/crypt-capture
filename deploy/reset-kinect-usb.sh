#!/bin/sh
# Prepare the Azure Kinect USB so it streams hands-off after a (cold) boot.
#
# Many Jetson "the camera won't start() until I physically replug it" cases are
# the USB port being put into **autosuspend** (power/control=auto): the device is
# enumerated but asleep, and only a replug wakes it. The DEFAULT action here is
# therefore gentle and safe: write power/control=on to the Kinect devices to
# disable autosuspend (and wake one that's already asleep). This never
# disconnects the device, so it can't break a working camera.
#
# Opt-in hard reset (KINECT_USB_RESET=reenumerate): additionally toggle the
# device's `authorized` flag to force a full USB re-enumeration (a software
# "replug"). This is heavier — it briefly makes the camera unavailable and needs
# a few seconds to settle — so it is NOT the default. Use it only if the
# no-suspend fix alone doesn't clear a cold-boot wedge.
#
# Runs as a root ExecStartPre before the node opens the camera. Non-fatal: always
# exits 0. Disable entirely with RESET_USB_ON_START=0 in /etc/default/kinect-node.
#
# Azure Kinect DK = USB vendor 045e (Microsoft); the internal hub + cameras use
# product ids 097a–097e. We match either vendor+pid or a "Kinect" product string.

if [ "${RESET_USB_ON_START:-1}" != "1" ]; then
    echo "kinect-usb: RESET_USB_ON_START=${RESET_USB_ON_START:-1}, skipping"
    exit 0
fi

mode="${KINECT_USB_RESET:-nosuspend}"
VID=045e
found=0
reenum=0
for dev in /sys/bus/usb/devices/*; do
    [ -f "$dev/idVendor" ] || continue
    [ "$(cat "$dev/idVendor" 2>/dev/null)" = "$VID" ] || continue
    pid=$(cat "$dev/idProduct" 2>/dev/null)
    prod=$(cat "$dev/product" 2>/dev/null)
    match=0
    case "$pid" in 097a|097b|097c|097d|097e) match=1 ;; esac
    case "$prod" in *[Kk]inect*) match=1 ;; esac
    [ "$match" = "1" ] || continue
    found=$((found + 1))

    # Gentle + safe: keep the device awake (disable autosuspend, wake if asleep).
    if [ -w "$dev/power/control" ]; then
        echo on > "$dev/power/control" 2>/dev/null || true
        echo "kinect-usb: autosuspend off for $(basename "$dev") ($VID:$pid $prod)"
    fi

    # Opt-in only: full re-enumeration (software replug).
    if [ "$mode" = "reenumerate" ] && [ -w "$dev/authorized" ]; then
        echo "kinect-usb: re-enumerating $(basename "$dev") ($VID:$pid $prod)"
        echo 0 > "$dev/authorized" 2>/dev/null || true
        sleep 1
        echo 1 > "$dev/authorized" 2>/dev/null || true
        reenum=1
    fi
done

[ "$found" -eq 0 ] && echo "kinect-usb: no Azure Kinect matched (continuing)"
[ "$reenum" -eq 1 ] && sleep 5   # let the device re-enumerate before the SDK opens it
exit 0
