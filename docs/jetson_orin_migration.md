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

## Storage

**Storage strategy — the node is a *bridge*, not an archive.** A recorded clip is
downloaded to (or livestreamed onto) the central machine and then **cleared from
the device**; the node never hoards footage. So on-device storage only has to hold
the OS + SDK + build tools + a small record-then-offload scratch buffer, not a
library of takes. That keeps the size requirement modest.

**Buy now — microSD to install the OS and start working:**
- **128 GB, UHS-I, A2-rated**, reputable brand: **SanDisk Extreme**,
  **Samsung PRO Plus**, or **Samsung EVO Plus**.
- Not 64 GB: JetPack + the Azure Kinect SDK + build tools eat ~25–30 GB, and you
  want room for build churn + the offload scratch buffer. 128 GB is the sweet spot
  and barely costs more. A2 random-IO makes boot/apt/compiling feel snappier.
- **Do NOT reuse the Nano's card by moving it** — it won't boot on the Orin and
  you'd destroy your rollback. Buy a new card; leave the Nano's as-is.
- You can flash the SD image yourself (step 1) or buy a pre-flashed "JetPack for
  Orin Nano" card to skip it.

**Buy for the long term — NVMe SSD (the devkit has an M.2 Key-M slot):**
- **500 GB NVMe M.2 2280, PCIe (NOT SATA).** Picks: **Samsung 970 EVO Plus 500 GB**
  (Gen3 — rock-solid on Orin, matches the devkit's PCIe **Gen3** slot), **WD Blue
  SN570**, or **Crucial P3**. Gen4 drives work but only run at Gen3 speed here, so
  don't pay the premium.
- Even though clips are transient, NVMe is the right medium once recording is
  routine: microSD wears out under sustained writes and its sustained write speed
  is the weakest link. NVMe is faster, far more durable, and can also boot the OS
  (~15–25 s vs ~45–60 s from SD). 500 GB is generous for a bridge; 1 TB only if you
  want margin.
- Recording data rate for sizing: full-res RVL depth + raw foreground RGB is
  **~0.5 GB/min ≈ ~30 GB/hour per camera** (up to ~1 MB/frame worst case). Since
  the node offloads and clears, this is a *buffer* budget, not an archive budget —
  even 500 GB is many sessions' worth of headroom.

## Which JetPack — recommendation: **JetPack 6.2 (Ubuntu 22.04)**

The Azure Kinect SDK is **archived (Aug 2024), x86-first, and 18.04-era**, so a
first instinct is "flash the oldest OS you can" (JetPack 5.1.x / Ubuntu 20.04).
Two current realities flip that:
1. **k4a is community-confirmed working on Ubuntu 22.04** via the same manual
   install you'd do on 20.04 (18.04 arm64 packages + the depth-engine binary), so
   the old-OS safety margin has largely evaporated.
2. **JetPack 5.1.x is now hard to put on a current Orin Nano** — NVIDIA moved to
   JetPack 6 as the default/production; SD images and SDK Manager push 6, and
   newer units expect JetPack-6-generation firmware. 5.1.x = more pain, less gain.

| JetPack | Ubuntu | Python | Notes |
|---|---|---|---|
| **6.2** | 22.04 | 3.10 | **Default.** Latest well-supported on Orin Nano + k4a works. |
| 5.1.x | 20.04 | 3.8 | Fallback only if 6.2 hits an unresolvable k4a dependency wall. |
| 7.x | 24.04 | — | **Avoid for now** — too new, Orin Nano support shaky, most likely to fight the 18.04-era depth engine. |

**Flash JetPack 6.2.** (The Orin Nano ships with a UEFI/QSPI firmware version;
if the SD image won't boot, run SDK Manager once from an x86 host to update
firmware, then it boots normally.) The node code is Python-3.6-safe so it runs
unchanged on 3.10.

## Migration steps

### 1. Flash the OS
Two options:
- **SD-image (simplest):** download the **JetPack 6.2 SD Card Image for Jetson
  Orin Nano Developer Kit** from NVIDIA, write it with Balena Etcher to the new
  card, boot, finish the Ubuntu first-boot wizard.
- **SDK Manager (from an Ubuntu x86 host):** flash the devkit over USB-C. Use
  this if the SD image won't boot (older factory firmware) — SDK Manager updates
  the QSPI firmware as part of the flow.

Confirm after boot:
```bash
lsb_release -a          # -> 22.04 (JetPack 6)
cat /etc/nv_tegra_release   # -> R36 (JetPack 6); confirms an Orin image
```
> **First boot needs a monitor + USB keyboard/mouse** for the `oem-config`
> end-user setup (username/locale). With no display it hangs on
> "A start job is running for End-user Configuration" — that's waiting for
> interactive input, not slow progress. Do the first boot with a screen attached,
> then go headless. Also: `nano` is **not** preinstalled — `sudo apt-get update &&
> sudo apt-get install -y nano` if you want it.

### 1b. Remote access (SSH) + the Xorg/GL session
Do the rest over SSH; the node draws no windows.
```bash
# on the Jetson (once, via the first-boot desktop):
sudo systemctl enable --now ssh                 # openssh ships with JetPack
sudo apt-get install -y avahi-daemon            # advertises <hostname>.local
hostnamectl                                     # note hostname + your username
# from your laptop:
ssh <user>@<hostname>.local
ssh-copy-id <user>@<hostname>.local             # passwordless (asks for pw once)
```
A `Host` alias in the laptop's `~/.ssh/config` (`HostName <hostname>.local`,
`User <user>`) lets you just `ssh orin`.

**The depth engine needs a live X (Xorg) session with a GL context** — same
requirement as the Nano, but JetPack 6 **defaults to Wayland**, which doesn't
provide it. Force Xorg + autologin so the session exists at boot:
```bash
sudo nano /etc/gdm3/custom.conf
#   [daemon]
#   WaylandEnable=false
#   AutomaticLoginEnable=true
#   AutomaticLogin=<user>
sudo reboot
```
Then camera processes over SSH need `export DISPLAY=:0`. If a fully monitor-less
Orin still fails with **`error code: 204`** (X up but no *hardware* GL), force an
EDID or add a ~$8 HDMI **dummy plug** — but try without it first; often the Xorg
autologin session alone is enough.

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

### 4. Azure Kinect SDK + depth engine — the hard part on 22.04 (aarch64)

There are **no native ARM64 packages for 20.04/22.04** (only 18.04), so install
the **18.04 arm64** packages onto 22.04. An 18.04-built binary runs fine on 22.04
(glibc/libstdc++ keep backward ABI compat). **The all-local-`.deb` route below is
the one that worked on hardware** — it sidesteps the apt-repo friction entirely.

> **⚠️ The `libsoundio1` gotcha (this is what bites you).** k4a depends on
> `libsoundio1`, which was **removed from Ubuntu 22.04** — it is *not* in any
> 22.04 repo, so `apt install libsoundio1` fails with "no installation candidate"
> and enabling `universe` does **nothing** (dead end — don't bother). You must
> pull the `.deb` from the **20.04 (focal)** arm64 archive and install it by hand,
> *before* the k4a packages. This is the single step that turns the whole thing
> from "impossible" into "works."

**Verified sequence (arm64, k4a 1.4.2 — newest):**
```bash
# 1. the missing dependency, from the Ubuntu 20.04 arm64 archive
wget http://ports.ubuntu.com/ubuntu-ports/pool/universe/libs/libsoundio/libsoundio1_1.1.0-1_arm64.deb

# 2. the Azure Kinect SDK debs (arm64, 1.4.2)
BASE=https://packages.microsoft.com/ubuntu/18.04/multiarch/prod/pool/main
wget $BASE/libk/libk4a1.4/libk4a1.4_1.4.2_arm64.deb
wget $BASE/libk/libk4a1.4-dev/libk4a1.4-dev_1.4.2_arm64.deb   # -dev needed: pyk4a compiles against it
wget $BASE/k/k4a-tools/k4a-tools_1.4.2_arm64.deb

# 3. install all four in ONE dpkg call so it resolves their interdependencies
sudo dpkg -i libsoundio1_1.1.0-1_arm64.deb \
             libk4a1.4_1.4.2_arm64.deb \
             libk4a1.4-dev_1.4.2_arm64.deb \
             k4a-tools_1.4.2_arm64.deb
sudo apt-get -f install     # pulls any remaining system deps
```
Accept the blue **EULA / debconf prompt** (Tab → OK/Yes) during install.

**The closed depth engine.** The `libk4a1.4` 1.4.2 deb above **already bundles**
`libdepthengine.so.2.0`, so normally nothing extra is needed. *Only if* the smoke
test reports the depth engine missing, extract it from the
`Microsoft.Azure.Kinect.Sensor` NuGet (`.nupkg`→`.zip`→unzip,
`linux/lib/native/arm64/release/`) and drop it in:
```bash
sudo cp libdepthengine.so.2.0 /usr/lib/aarch64-linux-gnu/ && sudo ldconfig
```

**udev rules:**
```bash
sudo wget -O /etc/udev/rules.d/99-k4a.rules \
  https://raw.githubusercontent.com/microsoft/Azure-Kinect-Sensor-SDK/develop/scripts/99-k4a.rules
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
JetPack 6 ships **Python 3.10** (3.8 on JetPack 5). Much simpler than the Nano:
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
