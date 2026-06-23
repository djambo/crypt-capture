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
If the camera isn't detected on first boot, power-cycle the Kinect / reboot
(known Nano quirk).

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
