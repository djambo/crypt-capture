# Migrating the capture node: Jetson Nano → Jetson Orin Nano

Step-by-step guide to move a working `kinect_node` from the 1st-gen **Jetson
Nano** (JetPack 4 / Ubuntu 18.04 / Python 3.6) to a new **Jetson Orin Nano
Developer Kit**. The Nano bring-up is in `jetson_setup.md`; this doc is the
*delta* — what changes on the Orin and why.

> The node only runs `node/` + `protocol/` (capture → RVL → stream). It never
> runs the central relay or the browser viewer. So this migration is entirely a
> `crypt-capture` concern.

## Read this first — three assumptions that don't hold on the Orin

1. **You can't downgrade JetPack to match the Nano.** The Orin Nano is a
   different SoC (Tegra234 vs the Nano's Tegra210); its **oldest** supported
   release is **JetPack 5.x (Ubuntu 20.04, Python 3.8)**. JetPack 4 / Ubuntu
   18.04 / Python 3.6 will never run on it. Our node code is already Python-3.6-
   safe, so it runs fine on 3.8/3.10 — nothing in `node/` or `protocol/` needs to
   change for the newer Python.
2. **You can't move the Nano's SD card over.** The OS image is built for the
   Nano's chip and won't boot on the Orin. Reflash a card with an Orin image.
   *Keep the Nano's card untouched* as a known-good rollback.
3. **The Orin *Nano* has no NVENC** (hardware video encoder) — only the Orin
   NX/AGX do. This does **not** block the current pipeline (color is shipped as
   raw foreground RGB, no codec; NVENC was always a deferred item). It only means
   future color compression is CPU/FFmpeg, not hardware. If hardware encode ever
   becomes a hard requirement, that's an Orin NX, not the Nano.

## SD card

- **Size:** 64 GB minimum, **128 GB recommended** (JetPack 6 + build tools + the
  Azure Kinect SDK + takes fill 64 GB fast). Use a **UHS-1, A2**-rated microSD
  (e.g. SanDisk Extreme / Samsung PRO). A2 random-IO helps boot/build feel.
- **Do NOT reuse the Nano's card by moving it** — it won't boot and you'd destroy
  your rollback. Buy a new card; leave the Nano's as-is.
- You can either flash the SD image yourself (below) or buy a pre-flashed
  "JetPack for Orin Nano" card to skip step 2.

## Which JetPack — recommendation: **JetPack 5.1.x (Ubuntu 20.04)**

The Azure Kinect SDK is **archived (Aug 2024), x86-first, and 18.04-era**. The
closer the OS is to 18.04, the less you fight the closed depth-engine binary.

| JetPack | Ubuntu | Python | Kinect risk | Pick it when |
|---|---|---|---|---|
| **5.1.x** | 20.04 | 3.8 | **Lower** — community has run k4a on 20.04 | **Default. Getting the Kinect working fast.** |
| 6.x | 22.04 | 3.10 | Higher — newer glibc/libsoundio vs an 18.04 binary | You want the longest support life and will debug deps |

**Start on JetPack 5.1.x.** Prove the camera streams, then consider 6.x later if
you want it — don't take the newer-OS risk while you're still bringing the sensor
up. (The Orin Nano ships from the factory with a UEFI/QSPI firmware version; very
new JetPack images may require a firmware update first. Flashing 5.1.x via SDK
Manager or the official SD image handles this for you.)

## Migration steps

### 1. Flash the OS
Two options:
- **SD-image (simplest):** download the **JetPack 5.1.x SD Card Image for Jetson
  Orin Nano** from NVIDIA, write it with Balena Etcher to the new card, boot,
  finish the Ubuntu first-boot wizard.
- **SDK Manager (from an Ubuntu x86 host):** flash the devkit over USB-C. Use
  this if the SD image won't boot (older factory firmware) — SDK Manager updates
  the QSPI firmware as part of the flow.

Confirm after boot:
```bash
lsb_release -a          # -> 20.04 (JetPack 5)
cat /etc/nv_tegra_release
nvcc --version || cat /etc/nv_tegra_release   # confirm it's an Orin image
```

### 2. Set the Orin to max performance (it has real headroom now)
```bash
sudo nvpmodel -m 0      # MAXN (all cores, full clocks)
sudo jetson_clocks      # pin clocks high
```
Unlike the Nano, the Orin Nano is a 6-core Cortex-A78AE — the RVL+color path that
was CPU-bound at ~92% of 30 fps on the Nano should hit the **30 fps sensor cap
comfortably**, likely at higher resolution / lower `--preview-stride`.

### 3. USB buffer + udev (same as the Nano)
```bash
sudo sh -c 'echo 256 > /sys/module/usbcore/parameters/usbfs_memory_mb'
# (the systemd unit re-applies this on boot; see step 7)
```
Each Kinect still needs its **own 5V barrel-jack supply** and an active USB3
cable. The cold-boot power-up-ordering rule from `jetson_setup.md` §9 still
applies (boot the Jetson with the Kinect powered, *then* it enumerates).

### 4. Azure Kinect SDK + depth engine — the hard part on 20.04

There are **no official ARM64 packages for 20.04/22.04** (only 18.04). Two routes,
in order of preference:

**Route A — install the 18.04 ARM64 packages onto 20.04 (try first).**
```bash
curl -sSL https://packages.microsoft.com/keys/microsoft.asc | sudo apt-key add -
# NOTE: 18.04 repo on purpose — there is no 20.04 arm64 multiarch repo
sudo apt-add-repository -y 'deb https://packages.microsoft.com/ubuntu/18.04/multiarch/prod bionic main'
sudo apt-get update
sudo apt-get install -y libk4a1.4 libk4a1.4-dev k4a-tools
```
If a dependency like `libsoundio1` is unmet on 20.04, install it manually (grab
the 18.04 `libsoundio1` .deb, or `sudo apt-get install -y libsoundio1`), then
retry. If the package's `libstdc++`/`libc` deps refuse, fall back to Route B.

**Route B — grab the .debs / NuGet payload manually and dpkg them.** Download the
`libk4a1.4`, `libk4a1.4-dev`, and `k4a-tools` `.deb`s (or the
`Microsoft.Azure.Kinect.Sensor` NuGet — rename `.nupkg`→`.zip`, unzip) and
`sudo dpkg -i` them, `sudo apt-get -f install` to resolve deps.

**The closed depth engine (both routes).** apt does not ship it. Extract
`libdepthengine.so.2.0` from the NuGet package at
`linux/lib/native/arm64/release/` and drop it next to the SDK libs:
```bash
sudo cp libdepthengine.so.2.0 /usr/lib/aarch64-linux-gnu/
sudo ldconfig
```

**udev rules:**
```bash
sudo cp scripts/99-k4a.rules /etc/udev/rules.d/    # from the SDK, or the valdivj repo
sudo udevadm control --reload-rules && sudo udevadm trigger
```

> ⚠️ **The Orbbec K4A wrapper does NOT help here** — it re-implements the k4a API
> for Orbbec Femto cameras and *explicitly does not support the original Azure
> Kinect DK*. It's only relevant if you later switch to a Femto Bolt (the Kinect's
> discontinued successor). For your existing Azure Kinect DK, you must use
> Microsoft's SDK + depth-engine binary as above.

> ⚠️ **Depth engine still needs a GPU/OpenGL context** (same code 204 as the
> Nano). Run from a desktop session or pass `DISPLAY`/`XAUTHORITY` into the
> service — see `jetson_setup.md` §9. The Orin has a real GPU, but the constraint
> is the same.

### 5. Smoke-test the sensor
```bash
k4aviewer                       # depth + color visible?  (needs a display/GL context)
# headless: k4arecorder -l 3 test.mkv && echo "capture OK"
```
If depth is missing but color/IR show, the depth engine `.so` isn't found — recheck
step 4's copy + `ldconfig`.

### 6. Python + deps
JetPack 5 ships **Python 3.8** (3.10 on JetPack 6). Much simpler than the Nano:
```bash
sudo apt-get install -y python3-pip
pip3 install --user numpy pyk4a
# if pyk4a can't find the lib:
export K4A_DLL_DIR=/usr/lib/aarch64-linux-gnu
```
No more `--no-deps` / `typing_extensions` gymnastics — those were Nano/3.6
workarounds. NumPy from apt or pip both work; the **NumPy fast-path RVL** now has
real cores to run on.

### 7. Get the code + run as a service
```bash
git clone https://github.com/djambo/crypt-capture.git
cd crypt-capture
sudo deploy/install-node-service.sh          # keep graphical.target (depth engine needs GL)
sudo nano /etc/default/kinect-node           # CENTRAL_HOST=auto (or a fixed IP), SENSOR_ID, DISPLAY/XAUTHORITY
sudo systemctl start kinect-node
journalctl -u kinect-node -f
```
Everything in `deploy/` is arch-independent and works unchanged. Because you're on
a fresh branch while bringing the Orin up, set `UPDATE_BRANCH` in
`/etc/default/kinect-node` to your working branch, or `AUTO_UPDATE=0` to freeze.

### 8. Validate end-to-end
Point it at the central relay and confirm frames + fps:
```bash
# on the laptop:
python3 -m central.preview_server
# on the Orin (or via the service):
python3 -m node.kinect_node --host auto --sensor 0 --frames 0 --profile
```
Expect the `--profile` line (`cap/RVL/color/send ms/f`) to show far lower RVL+color
than the Nano's `22+14 ms/f` — you should be sensor-capped at 30 fps, so try
dropping `--preview-stride` toward 1 and/or a wider depth FOV mode for a denser
cloud the Nano couldn't sustain.

## Rollback

The Nano + its SD card are untouched — if the Orin bring-up stalls on the depth
engine, pop the Nano's card back in and you're exactly where you were. Nothing in
this migration is destructive to the proven setup.

## If the Kinect fight isn't worth it

The Azure Kinect DK is discontinued and its SDK archived; the sanctioned
successor is the **Orbbec Femto Bolt** (Kinect-class ToF), which has a *maintained*
ARM64 SDK + the Orbbec K4A wrapper tested on Orin Nano. Not a migration step —
just the escape hatch if the closed 18.04 depth engine refuses to run on your OS.
