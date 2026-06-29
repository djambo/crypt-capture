# Jetson Nano — Azure Kinect bring-up

Goal: run one Azure Kinect on the 1st-gen Jetson Nano and stream a real take.
The Nano is the *proven* combo (JetPack 4.x / Ubuntu 18.04). It's CPU-slow, so
expect low fps with the pure-Python RVL — that's fine for validation.

> Reference community guide (follow it if these steps drift):
> https://github.com/valdivj/Azure-for-Kinect-Jetson-nano

## 1. OS
Flash **JetPack 4.x (Ubuntu 18.04)** to the Nano. Confirm: `lsb_release -a` → 18.04.

## 2. Azure Kinect SDK + depth engine (the fiddly part)
Add Microsoft's package source and install the SDK + tools:
```bash
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
sudo apt-add-repository -y 'deb https://packages.microsoft.com/ubuntu/18.04/multiarch/prod bionic main'
sudo apt-get update
sudo apt-get install -y libk4a1.4 libk4a1.4-dev k4a-tools
```
The closed **depth engine** is the one non-apt piece. If depth doesn't start
(`k4aviewer` shows color/IR but no depth), grab the ARM64
`libdepthengine.so.2.0` from the `Microsoft.Azure.Kinect.Sensor` NuGet package
(`/linux/lib/native/arm64/release/`) and copy it next to the SDK libs:
```bash
sudo cp libdepthengine.so.2.0 /usr/lib/aarch64-linux-gnu/
sudo ldconfig
```

## 3. USB permissions
```bash
# from the SDK's scripts (or the repo above):
sudo cp 99-k4a.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```
Each Kinect needs its **own 5V supply** and ideally an **active USB3 cable**.

## 4. Smoke test the sensor
```bash
k4aviewer          # you should see depth + color
# or headless:
k4arecorder -l 3 test.mkv && echo "capture OK"
```
If the camera isn't detected on first boot, the fix is power-up ordering: boot
the Jetson first (with the Kinect's barrel-jack power on), then connect/enumerate
the camera — see §9 "Kinect won't enumerate on a cold boot". (No software reset
substitutes for this on the Jetson.)

## 5. Python
```bash
sudo apt-get install -y python3-pip
pip3 install --user numpy pyk4a
```
If pyk4a can't find the lib, point it at it:
`export K4A_DLL_DIR=/usr/lib/aarch64-linux-gnu` (or pass `module_path` in code).

## 6. Get the code on the Nano
```bash
git clone https://github.com/djambo/crypt-capture.git
cd crypt-capture
```

## 7. Run (single sensor)
On the **central machine** (your laptop/desktop on the same LAN):
```bash
python3 -m central.recorder --port 9000 --sensors 1 --out takes/real1
```
On the **Nano** (replace CENTRAL_IP):
```bash
python3 -m node.kinect_node --host CENTRAL_IP --port 9000 --sensor 0 --frames 60
```
The recorder prints `recorded N complete frames` when the node finishes.

## 8. Verify the real take
On the central machine:
```bash
python3 - <<'PY'
import json, glob, os
from protocol import rvl
m = json.load(open("takes/real1/manifest.json"))
print("frames:", m["complete_frames"], "sensors:", m["num_sensors"])
fid = m["frame_ids"][0]; s0 = m["sensors"]["0"]
comp = open(f"takes/real1/frames/{fid:06d}/sensor0.depth.rvl","rb").read()
d = rvl.decompress(comp, s0["width"]*s0["height"])
print("valid depth px:", sum(1 for v in d if v), "/", len(d))
PY
```

## 9. Run on boot + restart on failure (systemd) — and go headless

Instead of VNC-ing in and launching the node by hand each boot, install it as a
**systemd service** so it starts automatically and relaunches if it dies (e.g.
the central relay was offline, or the camera hiccuped). The node has no internal
reconnect loop — it exits on a connection failure — so systemd supervising it is
the intended design.

From the repo root **on the Jetson**:
```bash
sudo deploy/install-node-service.sh           # keep the GUI
#   or, also drop the desktop for more headroom (see caveat below):
sudo deploy/install-node-service.sh --headless

sudo nano /etc/default/kinect-node            # set CENTRAL_HOST + SENSOR_ID
sudo systemctl start kinect-node
journalctl -u kinect-node -f                  # watch it stream / reconnect
```
Per-device settings (central IP/port, sensor id, extra flags like
`--preview-stride 2`) live in `/etc/default/kinect-node`; the unit
(`deploy/kinect-node.service`) stays generic. `CENTRAL_HOST` defaults to `auto`
(LAN discovery — see below); set it to a fixed IP/hostname only if you'd rather
pin it. It raises the USB buffer
(`usbfs_memory_mb=256`) as a root `ExecStartPre`, waits for the network, and
uses `Restart=always` / `RestartSec=3` with no start-rate limit, so it keeps
knocking until central comes up.

### Why headless (and why VNC hurts)

The node opens **no GUI windows** — it's pure capture + network streaming — and
on the Nano it's **CPU-bound at ~92% of the 30 fps sensor cap** (`RVL 22 +
color 14 ms/f` on 4 cores). A connected VNC session continuously re-encodes the
framebuffer, and the desktop compositor burns CPU/GPU/RAM (only 4 GB total) — all
competing with capture. So:
- **Don't keep a VNC client connected while streaming.** With the service you
  never need VNC to *launch* anything; SSH in to manage it.
- **`--headless` disables the desktop entirely** (`systemctl set-default
  multi-user.target`) — the biggest single win. Revert any time with
  `sudo systemctl set-default graphical.target`.

> ⚠️ **The depth engine needs a GPU/OpenGL context — confirmed on this rig.**
> The closed Azure Kinect depth engine fails to initialize without one. As a
> systemd service (no `DISPLAY`) it dies with `Depth engine create and
> initialize failed with error code: 204`, even though launching by hand from
> the desktop works. Two consequences:
>
> 1. **Keep `graphical.target`** (don't `set-default multi-user.target`) — the
>    depth engine can't run truly headless here. The performance win is then just
>    *not keeping a VNC client connected while capturing* (VNC's framebuffer
>    encode is what steals CPU; an idle desktop costs little).
> 2. **Give the service the X session's context** by adding `DISPLAY` +
>    `XAUTHORITY` to `/etc/default/kinect-node`. Get the values from a desktop
>    terminal (`echo $DISPLAY`; `echo ${XAUTHORITY:-$HOME/.Xauthority}`), set
>    them, `sudo systemctl restart kinect-node`. The service must start *after*
>    the user's X session exists — with `Restart=always` it just retries until
>    the desktop autologin is up, so enable desktop autologin for a clean boot.
>
> (Truly headless would need a real GPU GL context without a desktop — e.g. an
> Xvfb/EGL setup the depth engine accepts — which isn't reliable on this
> hardware. Revisit on an Orin if headless becomes a hard requirement.)

### Finding central without a fixed IP (`--host auto`)

If the laptop running central gets a new DHCP IP, you don't want to re-edit
every Jetson. With `CENTRAL_HOST=auto` (the default), the node **broadcasts on
the LAN** for the relay and connects to whoever answers — identified by a **rig
id**, not an address. The relay answers these broadcasts automatically (it runs
a small UDP discovery responder on `udp:9001`); nothing extra to start.

- One rig per LAN: leave the default rig id (`crypt`) on both sides — no config.
- Multiple rigs sharing a LAN: give each its own id — relay
  `python3 -m central.preview_server --rig-id studioB`, node
  `EXTRA_ARGS=... --rig-id studioB`.
- Verify from the Jetson: `journalctl -u kinect-node -f` shows
  `discovery: found central at <ip>:<port>` then frames. If discovery times out
  it exits and systemd retries in 3 s (e.g. central not up yet).

If your Wi-Fi blocks broadcast (AP/client isolation — common on guest networks),
discovery won't get through; fall back to a fixed `CENTRAL_HOST` using an **mDNS
hostname** (`mylaptop.local`, if the laptop runs Bonjour/Avahi) or a **DHCP
reservation** on the router so the laptop keeps one IP. Use Ethernet for the rig
where you can — it's the intended transport and doesn't isolate clients.

### Auto-update the code on boot (push → reboot → runs latest)

With no GUI you can't pull updates by hand. The service does it for you: a
pre-start step (`deploy/update-node.sh`) **fetches and hard-resets the code to
the remote** before launching, so your workflow becomes *push to the branch →
reboot the Jetson → it runs the latest*. It's **best-effort**: if the Jetson is
offline or can't reach the remote, it logs and runs the on-disk code anyway
(capture is never blocked by a failed pull).

Config in `/etc/default/kinect-node`:
```sh
AUTO_UPDATE=1          # 0 to freeze the on-device code
UPDATE_BRANCH=main     # track this branch (set to your feature branch while testing)
```
- It runs as the service **User=**, so that user must own the clone and have pull
  access. Confirm after a reboot: `journalctl -u kinect-node | grep update-node`
  → `now at <sha> (origin/main)`.
- **Hard reset discards on-device edits** — this is an appliance, edit via push,
  not on the Jetson. (Switch to `git pull --ff-only` in the script if you must
  keep local edits.)
- It updates **code only**. Changes to the unit itself or to
  `/etc/default/kinect-node` (e.g. a new CLI flag) still need a re-run of
  `deploy/install-node-service.sh`.
- A broken commit on the tracked branch will be pulled and (with `Restart=always`)
  crash-loop every node — so push to `main` only what you've tested, or keep the
  Jetsons on a branch you promote deliberately.

**Private repo?** A non-interactive service can't type a password (the script
sets `GIT_TERMINAL_PROMPT=0` so it fails fast rather than hanging). Give the
device non-interactive pull access one of these ways:
- **SSH deploy key** (recommended): `ssh-keygen -t ed25519` as the service user,
  add the public key as a read-only Deploy Key on the GitHub repo, and set the
  clone's remote to SSH (`git remote set-url origin git@github.com:djambo/crypt-capture.git`).
- **Cached HTTPS token**: `git config --global credential.helper store` then do
  one manual `git pull` with a PAT to cache it.

A **public** repo needs none of this — anonymous HTTPS pull just works.

### Kinect won't enumerate on a cold boot — power-up ordering, not software

After a cold boot the Kinect's **depth camera** (`045e:097c`) often doesn't appear
on USB, so the SDK fails to open the device (`libusb device(s) are all
unavailable` / `LIBUSB_ERROR_IO` reading the BOS descriptor). The cause is
hardware **power-up ordering**: the camera must be powered and ready *before* the
USB host scans the bus, or the depth processor is missed. A physical replug fixes
it because it re-enumerates the device once it's ready.

**There is no reliable software fix for this on the Jetson** — we tried (a
pre-start USB re-enumeration / autosuspend toggle) and it did more harm than
good: toggling the device made the depth camera drop off the bus entirely and
crash-loop. That experiment has been removed. Don't reintroduce per-start USB
resets here.

The reliable workflow:
1. **Boot the Jetson first** (with the Kinect's barrel-jack power on so the camera
   is ready), **then** connect — or the camera was already connected and powered
   before boot. The point is the depth camera must be live when the host
   enumerates.
2. The `kinect-node` service **retries continuously** (`Restart=always`), so the
   moment the camera enumerates it grabs it and streams — no manual launch.

If the camera still won't enumerate: confirm the Kinect's **own 5V barrel-jack
supply** is connected (the Jetson ports can't power the depth camera alone) and
its status **LED is solid white**. A pulsing LED or a missing `045e:097c` in
`lsusb` is a power/readiness problem, not a software one. As a last resort a
powered USB3 hub with switchable per-port power + [`uhubctl`](https://github.com/mvp/uhubctl)
can power-cycle the port on a schedule, but that's hardware, not part of this
service.

## Notes / known limits
- **Speed:** pure-Python RVL is ~tens of ms/frame; on the Nano you'll get only a
  few fps. That's expected for validation. Production: a NumPy-vectorized or
  C/Cython RVL, and NVENC for color.
- **No matting yet:** the node uses a depth range-clip. Add RVM/BGMv2 per-view
  for clean edges once the spine is proven (heavy on the Nano — better on an
  Orin NX / x86 node).
- **Multi-sensor:** wire the 3.5mm sync cables, run one node `--sync master` and
  the rest `--sync sub --sub-delay-us <160*index>`, start subs before master.
  Frame-id alignment across nodes (from device timestamps) is a Phase-2 item.
