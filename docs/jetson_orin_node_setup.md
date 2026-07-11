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

**WiFi power saving** must be OFF on a node that streams over WiFi (the radio
dozes between beacons → RTT spikes and multi-hundred-ms stalls). The service
installer (§9) handles it via `deploy/disable-wifi-powersave.sh`; on an
already-provisioned node run it once directly, then reboot once so the
driver-level modprobe options load:
```bash
sudo deploy/disable-wifi-powersave.sh && sudo reboot
# verify after: iw dev wlan0 get power_save   -> "Power save: off"
```
Do NOT just edit `NetworkManager.conf` — Ubuntu ships
`/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf` (powersave = 3),
which is read after the main file and silently wins; and the Realtek rtw88
driver (the devkit's RTL8822CE) dozes on its own below NetworkManager anyway.
The script covers all the layers (NM `zz-*` drop-in, per-connection profiles,
a dispatcher hook on every interface-up, rtw88/iwlwifi modprobe options).

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
  cold-boot enumeration** issue — power-cycle the Kinect's 5 V adapter; see §11.

## 8. Python + pyk4a
`pip3` is **not** preinstalled on a fresh JetPack 6.2 (`pip3: command not found`)
— install it first:
```bash
sudo apt-get install -y python3-pip
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
NODE_PROFILE=auto      # device-class defaults from deploy/profiles/ (Orin ->
                       # full res + skeleton pose); auto-detected, leave as-is
EXTRA_ARGS=            # per-device tweaks only; appended AFTER the profile
```
```bash
sudo systemctl restart kinect-node
journalctl -u kinect-node -f      # -> "discovery: found central" then "sensor 0: … fps … pts"
```
The service handles the rest: `Restart=always`, USB-buffer fix on boot, LAN
discovery, and git self-update (push → reboot → runs latest). It borrows the
autologin session's GL context via `DISPLAY`/`XAUTHORITY` — **no error 204**.

## 10. Skeleton pose — GPU inference (one-time per Orin)
The service's **orin profile already passes the pose flags** (`--pose-model
models/movenet.onnx --pose-trt`), so there is nothing to configure — until
this install is done the node just logs "pose disabled" and streams normally.

```bash
cd ~/crypt-capture

# onnxruntime-gpu: there are NO aarch64 wheels on PyPI — use NVIDIA's Jetson
# index (the .io host; the older .dev mirror is dead).
# "numpy<2" is REQUIRED: pyk4a was compiled on-device against NumPy 1.x and
# dies with "a module compiled using NumPy 1.x cannot be run in NumPy 2.x"
# if anything drags in NumPy 2.
pip3 install --user onnxruntime-gpu "numpy<2" \
    --extra-index-url https://pypi.jetson-ai-lab.io/jp6/cu126
python3 -c "import onnxruntime as o; print(o.get_available_providers())"
# want: ['TensorrtExecutionProvider', 'CUDAExecutionProvider', 'CPUExecutionProvider']

# The model: MoveNet single-pose THUNDER (256 px — the accurate variant;
# Lightning is faster but noticeably jitterier, and TensorRT has huge
# headroom either way). models/ is gitignored, so the service's self-update
# never touches the download.
mkdir -p models
curl -L -o models/movenet.onnx \
  https://huggingface.co/Xenova/movenet-singlepose-thunder/resolve/main/onnx/model.onnx
ls -lh models/movenet.onnx        # ~25 MB; a few KB = an error page, re-download

# Optional: bench the model alone (expect a few ms/infer on TensorRT):
python3 -m node.pose models/movenet.onnx --trt

sudo systemctl restart kinect-node
journalctl -u kinect-node -f
```
The **first start compiles the TensorRT engine** (~1–3 min: `pose: building
the TensorRT engine…`), cached in `models/trt_cache` — every later start is
instant. Healthy log lines:
```
sensor 0 pose: model models/movenet.onnx (input 256x256 NHWC, ...) on Tensorrt/CUDA/CPU
sensor 0 pose: 28.5 fps (12 ms/infer = pre 2 + run 7, 7 joints, 0% gated)
```
with the frame line still pinned at 30 fps (pose runs in its own process and
can never slow the cloud). If you swap the model file later, also
`rm -rf models/trt_cache` (the cached engine belongs to the old model).

(Nodes that can't run inference — e.g. an old Nano — get skeletons from the
relay instead: `preview_server --pose-model models/movenet.onnx` on the
laptop. See `docs/skeleton_pose.md`.)

## 11. Cold-boot power ordering (know this)
The depth camera (`045e:097c`) sometimes doesn't enumerate if it isn't powered/
ready when the host scans USB. Observed behaviour: with the Kinect powered and
connected, a Jetson **reboot** cycles the USB bus and it re-enumerates cleanly on
its own → fully automatic. If a cold boot ever misses it, **cycle the Kinect's
5 V adapter once** (USB stays connected) and the retrying service grabs it in ~3 s.
There is **no reliable software USB-reset** on the Jetson (tried and removed).
To fully automate: a **`uhubctl`-capable powered USB hub** (or a smart plug on the
Kinect adapter) can power-cycle the port a few seconds after boot.

## 12. Validate end-to-end
```bash
# on the laptop / central:
python3 -m central.preview_server
```
Open the **crypt viewer** at the laptop, confirm the live cloud, then hit
**Capture Background** (step out during the 3 s countdown, step back in).
Point count drops to ~30–40 k and fps pins to **30**, with the room frozen as
the environment layer. Streaming the *full unmasked room* (250–400 k pts,
~1.3 MB/f) is network-bound and will read 5–9 fps — that's expected; background
subtraction is the lever, not faster hardware. Step in front of the camera and
confirm the **skeleton markers** track you (skeletons layer toggle on).

With two or more nodes streaming: **Rough Align** (walk a slow "L", visible to
every camera — the status line should report tier **skeleton**), then **Detect
Floor**. Clouds registered, floors flush on the grid — the rig is at parity.

---

## Per-node checklist (repeat for each Jetson)
- [ ] Flash JetPack 6.2, first-boot with a display (§1)
- [ ] SSH + avahi + key (§2), then unplug the monitor
- [ ] Xorg + autologin, reboot (§3)
- [ ] Kinect SDK debs + udev rules + replug (§5–6)
- [ ] `CAPTURE OK` smoke test (§7)
- [ ] pyk4a (§8)
- [ ] Service + `/etc/default/kinect-node` with a **unique `SENSOR_ID`** (§9)
- [ ] WiFi powersave OFF (§4 — the installer runs the script; verify
      `iw dev wlan0 get power_save` → off, reboot once for the driver options)
- [ ] Skeleton pose: `onnxruntime-gpu "numpy<2"` + MoveNet Thunder (§10)
- [ ] Confirm streaming + skeleton in the viewer (§12)

**What differs per node:** just `SENSOR_ID` (0,1,2,3). Discovery finds central
automatically; if two rigs share a LAN, also set a matching `--rig-id` on both
node (`EXTRA_ARGS`) and relay.

## Note
Earlier pyk4a builds threw `extrinsic: no COLOR->DEPTH from pyk4a ('Calibration'
has no 'convert_3d_to_3d')`, breaking `depth_to_color` registration. Fixed in the
node: the extrinsics now come from pyk4a's public `get_extrinsic_parameters`
(with a `color_to_depth_3d` fallback), so both the COLOR->DEPTH registration and
the factory IMU ACCEL->DEPTH extrinsic work on current pyk4a. If you saw that
warning before, `git pull` (the service self-updates on restart) clears it.
