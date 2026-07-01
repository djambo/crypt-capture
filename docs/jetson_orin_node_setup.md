# Provisioning a Jetson Orin Nano capture node (runbook)

Copy-paste, top-to-bottom setup for a **new** Azure Kinect capture node on a
**Jetson Orin Nano Developer Kit**. This is the streamlined "just do these steps"
runbook — every command here was verified on hardware. For *why* each step exists
and the dead-ends we ruled out, see `jetson_orin_migration.md`.

The node runs only `node/` + `protocol/` (capture → RVL → stream). It never runs
the central relay or the browser viewer.

---

## 0. What you need

- **Jetson Orin Nano Developer Kit**
- **microSD ≥128 GB, UHS-I, A2** (SanDisk Extreme / Samsung EVO Plus). (Long-term:
  an NVMe M.2 2280 PCIe SSD in the devkit's slot; SD is fine for a bridge node.)
- **Azure Kinect DK** + **its own power** — the bundled USB-C Y-cable with the 5 V
  wall adapter (USB-C data to the Jetson, power leg to the adapter).
  - ⚠️ **Power the Kinect ONLY from its own ~5 V adapter.** Never the Jetson's 19 V
    barrel (destroys the camera). The Orin's USB-C is **data-only (no power
    delivery)**, so it can't power the Kinect either.
- **Monitor + USB keyboard/mouse** — for the first boot ONLY (the `oem-config`
  wizard needs them; with no display it hangs on "A start job is running for
  End-user Configuration"). After that it's headless over SSH.
- **Ethernet** (recommended — the intended rig transport; avoids Wi-Fi jitter).

---

## 1. Flash JetPack 6.2
- Download the **JetPack 6.2 SD Card Image for Jetson Orin Nano Developer Kit**,
  write it with **Balena Etcher**, boot with a monitor + keyboard attached, and
  complete the Ubuntu first-boot wizard (user, locale, network).
- (If the SD image won't boot: run **SDK Manager** once from an x86 Ubuntu host to
  update the QSPI firmware, then flash.)
- **Avoid JetPack 7** (too new for the archived Kinect SDK). Never accept an Ubuntu
  release upgrade (23.x/24.04) later — it breaks JetPack.

Confirm:
```bash
lsb_release -a              # -> 22.04
cat /etc/nv_tegra_release   # -> R36
sudo apt-get update && sudo apt-get install -y nano   # nano is NOT preinstalled
```

## 2. Remote access (SSH) — go headless after this
```bash
# on the Jetson:
sudo systemctl enable --now ssh
sudo apt-get install -y avahi-daemon        # advertises <hostname>.local
hostnamectl                                 # note hostname + username
# from your laptop:
ssh <user>@<hostname>.local
ssh-copy-id <user>@<hostname>.local         # passwordless (asks for pw once)
```

## 3. Xorg + autologin (the depth engine needs a GL context)
JetPack 6 defaults to Wayland, which doesn't provide the OpenGL context the closed
depth engine needs. Force Xorg + autologin so the session exists at boot:
```bash
sudo nano /etc/gdm3/custom.conf
```
```ini
[daemon]
WaylandEnable=false
AutomaticLoginEnable=true
AutomaticLogin=<user>
```
```bash
sudo reboot        # reconnect over SSH after
```

## 4. Performance
```bash
sudo nvpmodel -m 0
sudo jetson_clocks
```

## 5. Azure Kinect SDK (the verified all-local-`.deb` route)
`libsoundio1` (a k4a dependency) was **removed from Ubuntu 22.04** — apt/`universe`
can't find it, so pull it from the 20.04 archive. Then the k4a debs. Install all
four in one `dpkg` call:
```bash
# 1. the missing dependency, from the Ubuntu 20.04 arm64 archive
wget http://ports.ubuntu.com/ubuntu-ports/pool/universe/libs/libsoundio/libsoundio1_1.1.0-1_arm64.deb

# 2. Azure Kinect SDK debs (arm64, 1.4.2)
BASE=https://packages.microsoft.com/ubuntu/18.04/multiarch/prod/pool/main
wget $BASE/libk/libk4a1.4/libk4a1.4_1.4.2_arm64.deb
wget $BASE/libk/libk4a1.4-dev/libk4a1.4-dev_1.4.2_arm64.deb
wget $BASE/k/k4a-tools/k4a-tools_1.4.2_arm64.deb

# 3. one dpkg call resolves the interdependencies
sudo dpkg -i libsoundio1_1.1.0-1_arm64.deb \
             libk4a1.4_1.4.2_arm64.deb \
             libk4a1.4-dev_1.4.2_arm64.deb \
             k4a-tools_1.4.2_arm64.deb
sudo apt-get -f install
```
Accept the blue **EULA / debconf prompt** (Tab → OK/Yes). The 1.4.2 `libk4a` deb
already bundles `libdepthengine.so.2.0`, so no NuGet extraction is needed.

## 6. udev rules (REQUIRED — the deb does not install them)
Without these, a non-root capture fails with **`libusb device(s) are all
unavailable / k4a_device_open() failed`** — looks like a power problem but is
permissions:
```bash
sudo wget -O /etc/udev/rules.d/99-k4a.rules \
  https://raw.githubusercontent.com/microsoft/Azure-Kinect-Sensor-SDK/develop/scripts/99-k4a.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```
Then **unplug/replug the Kinect USB** so the rules apply on re-enumeration.

## 7. Smoke-test the camera
Kinect on its own power (LED solid white), USB in a USB3 port:
```bash
export DISPLAY=:0
k4arecorder -l 3 /tmp/test.mkv && echo "CAPTURE OK"
```
- `CAPTURE OK` → done, move on.
- `Failed to open display` → Xorg session not up (redo step 3 / attach a display).
- `libusb ... unavailable` → run `lsusb | grep 097c`. If the **depth camera
  `045e:097c` is missing** (color `097d`/mics/hubs present), it's a **power/
  cold-boot enumeration** issue — power-cycle the Kinect's 5 V adapter; see §10.

## 8. Python + pyk4a
```bash
pip3 install --user numpy pyk4a
python3 -c "import pyk4a; print('pyk4a OK')"
# if the build can't find the SDK:
#   export K4A_INCLUDE_DIR=/usr/include K4A_LIB_DIR=/usr/lib/aarch64-linux-gnu
#   pip3 install --user pyk4a --no-build-isolation
```

## 9. Install the boot service
```bash
git clone https://github.com/djambo/crypt-capture.git
cd crypt-capture
sudo deploy/install-node-service.sh

# find the autologin session's X auth path:
ps -C Xorg -o args= | grep -o '\-auth [^ ]*'     # -> /run/user/1000/gdm/Xauthority

sudo nano /etc/default/kinect-node
```
Set:
```sh
CENTRAL_HOST=auto                                  # LAN discovery of the relay
SENSOR_ID=0                                         # UNIQUE per node (0..N-1)
DISPLAY=:0
XAUTHORITY=/run/user/1000/gdm/Xauthority           # from the ps command above
AUTO_UPDATE=1
UPDATE_BRANCH=main
EXTRA_ARGS=--preview-stride 1                       # full res; Orin has the headroom
```
```bash
sudo systemctl restart kinect-node
journalctl -u kinect-node -f      # -> "discovery: found central" then "sensor 0: … fps … pts"
```
The service handles the rest: `Restart=always`, USB-buffer fix on boot, LAN
discovery, and git self-update (push → reboot → runs latest). It borrows the
autologin session's GL context via `DISPLAY`/`XAUTHORITY` — **no error 204**.

## 10. Cold-boot power ordering (know this)
The depth camera (`045e:097c`) sometimes doesn't enumerate if it isn't powered/
ready when the host scans USB. Observed behaviour: with the Kinect powered and
connected, a Jetson **reboot** cycles the USB bus and it re-enumerates cleanly on
its own → fully automatic. If a cold boot ever misses it, **cycle the Kinect's
5 V adapter once** (USB stays connected) and the retrying service grabs it in ~3 s.
There is **no reliable software USB-reset** on the Jetson (tried and removed).
To fully automate: a **`uhubctl`-capable powered USB hub** (or a smart plug on the
Kinect adapter) can power-cycle the port a few seconds after boot.

## 11. Validate end-to-end
```bash
# on the laptop / central:
python3 -m central.preview_server
```
Open the **crypt viewer** at the laptop, confirm the live cloud, then hit
**Capture Background** (step out, capture, step back in). Point count drops to
~30–40 k and fps pins to **30**. Streaming the *full unmasked room* (250–400 k pts,
~1.3 MB/f) is network-bound and will read 5–9 fps — that's expected; background
subtraction is the lever, not faster hardware.

---

## Per-node checklist (repeat for each Jetson)
- [ ] Flash JetPack 6.2, first-boot with a display (§1)
- [ ] SSH + avahi + key (§2), then unplug the monitor
- [ ] Xorg + autologin, reboot (§3)
- [ ] Kinect SDK debs + udev rules + replug (§5–6)
- [ ] `CAPTURE OK` smoke test (§7)
- [ ] pyk4a (§8)
- [ ] Service + `/etc/default/kinect-node` with a **unique `SENSOR_ID`** (§9)
- [ ] Confirm streaming in the viewer (§11)

**What differs per node:** just `SENSOR_ID` (0,1,2,3). Discovery finds central
automatically; if two rigs share a LAN, also set a matching `--rig-id` on both
node (`EXTRA_ARGS`) and relay.

## Known non-blocker
`extrinsic: no COLOR->DEPTH from pyk4a ('Calibration' has no 'convert_3d_to_3d')`
— the installed pyk4a lacks that method, so **`depth_to_color`** alignment won't
register to the depth frame. `color_to_depth` (default path) and the IMU are
unaffected. Revisit with a pyk4a build that restores `convert_3d_to_3d` only if
you want the denser `depth_to_color` cloud.
