# Node hardware — findings & recommendation

Goal: one Azure Kinect DK per small edge node doing capture + on-device AI
matting + encode + LAN streaming.

## Verdict

**Lowest-risk node: x86 mini-PC + a small NVIDIA GPU** (Ubuntu 18.04, or 20.04
with the depth-engine workaround). The Azure Kinect SDK is archived (EOL Aug
2024) and x86-first; this runs the official SDK + Body Tracking trivially, has
NVENC, and avoids ARM porting.

**Jetson is viable but fiddly for *this* sensor:**
- Proven combo is *old*: **Jetson Nano / Xavier on JetPack 4.x (Ubuntu 18.04)**
  with SDK 1.4.x — community guides exist.
- **Orin (JetPack 5/6, Ubuntu 20.04/22.04)** has no official Microsoft ARM repo;
  build from source + hand-place the closed `libdepthengine.so.2.0` (ARM64 build
  ships in the `Microsoft.Azure.Kinect.Sensor` NuGet). Works "with effort", not
  plug-and-play.
- **Body Tracking does NOT run on ARM/Jetson** (Microsoft confirms). Only
  matters if you want skeleton-based isolation — our plan uses *matting*, so
  this isn't a blocker for us.
- **Jetson Orin *Nano* has no NVENC** — if Jetson, use **Orin NX / AGX**, never
  Orin Nano, for hardware color encode. (The 1st-gen Maxwell Nano *does* have
  NVENC.)
- USB3: give each Kinect its own 5V supply + active cable; verify Intel/TI/
  Renesas xHCI controller (ASMedia fails).

**Use the 1st-gen Jetson Nano you already own to validate this spine for free**
(it's the proven SDK combo), then choose production nodes after seeing real
numbers.

## Depth compression: RVL

Microsoft Research "Fast Lossless Depth Image Compression" (Wilson, ISS 2017) —
tiny, lossless, designed for many depth cameras over LAN. Implemented in
`protocol/rvl.py`. Temporal RVL improves streams further.

## Prior art to evaluate before writing more transport

**Sensor Stream Pipe (Moetsi)** already does Kinect capture → compression → LAN
streaming. Evaluate building on it.

## Sources

- Azure Kinect SDK ARM / Jetson: https://github.com/microsoft/Azure-Kinect-Sensor-SDK/issues/871 · https://github.com/microsoft/Azure-Kinect-Sensor-SDK/issues/1961
- depth engine ARM64: https://github.com/microsoft/Azure-Kinect-Sensor-SDK/issues/568 · https://github.com/microsoft/Azure-Kinect-Sensor-SDK/blob/develop/docs/depthengine.md
- Body Tracking not on ARM: https://learn.microsoft.com/en-us/answers/questions/687528/
- Jetson Nano guide: https://github.com/valdivj/Azure-for-Kinect-Jetson-nano
- Orin Nano has no NVENC: https://docs.nvidia.com/jetson/archives/r36.2/DeveloperGuide/SD/Multimedia/SoftwareEncodeInOrinNano.html
- RVL paper: https://www.microsoft.com/en-us/research/uploads/prod/2018/09/p100-wilson.pdf
- Sensor Stream Pipe: https://sensor-stream-pipe.moetsi.com/linux
- Azure Kinect discontinued; Orbbec Femto Bolt successor: https://github.com/microsoft/Azure-Kinect-Sensor-SDK/issues/1971
