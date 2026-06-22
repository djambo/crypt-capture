# crypt-capture

Distributed multi-Kinect volumetric capture → web library pipeline.

Capture short volumetric clips of a single performer with **4 Azure Kinect DK**
sensors (one per edge node), clean them, fuse them into a solid surface, and
serve them from a web app that plays and creatively renders the library.

This repo is the **capture + processing + delivery** system. The real-time
*creative renderer* lives alongside in the `crypt` three.js project.

---

## Architecture

```
[4 Azure Kinect DK]  ── HW-synced via 3.5mm daisy-chain (frame-accurate) ──┐
   │ each USB3 → its own edge node                                          │
[Node ×4]  capture → AI matte (RVM/BGMv2) → RVL depth + NVENC color → stream┘
   │                                  (only the masked human is sent)
   │  TCP, one synced Frame per capture, grouped by frame_id
[Central]  receive → record take → (offline) calibrate → TSDF fuse → mesh seq
   │
[Web app]  browse / preview the library  +  creative render (particles, FX)
```

**Why one sensor per node:** 4 Kinects on a single PC saturate USB3 controllers
(the #1 failure of single-box rigs). One sensor per node sidesteps that *and*
distributes the expensive AI matting across the nodes' GPUs, so each node
streams **only the masked foreground** — far less network data, no central ML
bottleneck.

**Sync vs trigger:** frame sync is *hardware* (daisy-chained sync cables between
the cameras, independent of which node hosts them). The central app only sends a
network *trigger* (arm / record / stop); the hardware sync guarantees frames
with the same `frame_id` are simultaneous.

---

## Key decisions (and why)

- **Representation: TSDF fusion → watertight mesh sequence** (approach "B").
  Real depth sensors give clean metric geometry, so we lean into geometry, not
  Gaussian splatting (which would throw the depth away and re-estimate it via
  per-scene training). Fusion also *dissolves multi-sensor seams* by averaging
  all views into one signed-distance field. Trade-off: variable topology per
  frame → stream as per-frame meshes (not VAT). Upgrade path "C": fit/track a
  human template (SMPL-X) for consistent topology → VAT-able + tiny.
- **Cleanup: per-view AI matting** (Robust Video Matting, or BackgroundMattingV2
  with a background plate since the rig is static) beats sparse skeleton
  clipping for clean hair/finger edges; run it **on the node** to cut bandwidth.
  Note RVM is GPL-3.0 — check licensing for a closed product.
- **Depth transport: RVL** (Microsoft Research's lossless depth codec, designed
  for exactly this — many Kinects over LAN). ~12–14× on masked depth, lossless.
  See `protocol/rvl.py`.
- **Color transport: NVENC** H.264/H.265 on the node (placeholder in the sim).
- **Web delivery: glTF + `meshopt` (NOT Draco)** for animated sequences — Draco
  is static-geometry-only and breaks morph/animation. Vertex color for now;
  texture-as-video later if photoreal is needed.
- **Node hardware:** the Azure Kinect SDK is archived and x86-first; **Body
  Tracking does not run on ARM/Jetson**, and new Jetson OS (Orin/JetPack 5-6)
  fights the old depth-engine binary. Lowest-risk node is an **x86 mini-PC + a
  small NVIDIA GPU**. A **1st-gen Jetson Nano** (JetPack 4 / Ubuntu 18.04) is a
  *proven* combo and a great zero-cost way to validate this spine — too weak for
  fast matting, fine for capture+stream. See `docs/hardware.md`.

---

## Status

| Piece | State |
|---|---|
| RVL depth codec (`protocol/rvl.py`) | ✅ implemented + tested (lossless, ~14×) |
| Wire protocol (`protocol/frame.py`) | ✅ |
| Simulated node (`node/sim_node.py`) | ✅ (no hardware needed) |
| Central recorder (`central/recorder.py`) | ✅ records synced takes |
| End-to-end spine (`scripts/run_demo.py`) | ✅ tested, 4 sensors → take |
| Real Kinect node (pyk4a + matting + NVENC) | ⬜ next (needs hardware) |
| Calibration + TSDF fusion (offline) | ⬜ |
| Mesh-sequence export (glTF/meshopt) | ⬜ |
| Web preview + creative renderer | ⬜ (reuse `crypt`) |

The **hardware-independent half of Phase 1 is done and tested** — the real node
drops into the same protocol when sensors are ready.

---

## Run the spine (no hardware)

```bash
python3 scripts/run_demo.py --sensors 4 --frames 15
```

Spins up the recorder + 4 simulated nodes on loopback, records a synchronized
take to `takes/demo/`, and verifies depth decodes losslessly.

Standalone:
```bash
python3 -m central.recorder --port 9000 --sensors 4 --out takes/t1
python3 -m node.sim_node --port 9000 --sensor 0 --frames 30   # ×4, sensor 0..3
```

Pure stdlib for the spine (Python 3.8+). Real-node and offline-processing
dependencies (pyk4a, torch, open3d, …) are introduced with those modules.

## Roadmap

- **Phase 1** — spine (✅ sim) → swap in real Kinect node.
- **Phase 2** — calibration (marker + ICP) + TSDF fusion → watertight mesh/frame.
- **Phase 3** — web library app: capture trigger, recording browser, playback.
- **Phase 4** — creative FX rendering; SMPL-X template tracking (approach C) for
  fixed-topology / streamable compression.
