# CLAUDE.md — project context & handoff

> This file is auto-loaded by Claude Code. It distills a long design+build
> session so any new session can continue without re-deriving everything.

> **⚠️ KEEP THIS FILE CURRENT (instruction to Claude).** This is a living
> document — it is only useful if it stays accurate. Whenever a session changes
> something that future-you would need to know, **update this file as part of
> that same work, before ending the turn.** That includes: a decision made or
> reversed; a phase/status changing (e.g. a roadmap item completed); the repo
> layout, run commands, or architecture changing; a new environment gotcha
> discovered; or an open item resolved. Prefer editing the relevant section over
> appending. If a decision here is contradicted by newer reality, fix it (don't
> leave both). Keep it concise — distill, don't dump. A stale CLAUDE.md is worse
> than none, so treat updating it as part of "done," not an afterthought.

## What this project is

A **live, networked capture web app** for volumetric video of a single
performer, captured with **4 Azure Kinect DK** sensors (1 per Jetson). The
central web app connects to the remote Jetsons over Ethernet, **live-streams
their point clouds in real time**, and on a **trigger** has every node
**record a full-fidelity clip to its own local disk** for **download** and
post-processing. End-state = 4 sensors fused into one aligned cloud/surface;
the recorded clips also feed the creative renderer (particles/FX are a goal).

> **Scope note (2026-06):** the product is the *real-time app*; recording is one
> mode of it, not the whole thing. Recording is **local-on-node, downloaded
> after** (the wire only carries live preview + the download). Build order is
> **live preview first**, then record/download, then N nodes, then multi-view
> alignment/fusion. Full architecture + feasibility (bandwidth math, transport
> choices, Orin-vs-Nano eval, MVP milestones) live in
> **`docs/realtime_architecture.md`** — read it before building.

## Vision / North Star (the final goal — build toward it)

**crypt** is an "edge of reality" framework. The end goal is a **WebXR/VR
experience** where the user **cannot tell prerecorded from real-time** volumetric
content sharing the same space:

- **prerecorded** 4-Kinect volumetric clips, AND
- **real-time** volumetric streams of whoever physically enters the capture
  volume — including **the user's own body**: pre-record yourself, then **step
  out of your own body** and watch the clip play back where you stood.
- Far future: stream between **two locations** → volumetric **teleportation**.

Two consequences this North Star forces *early* (don't defer them to fusion):

1. **One shared world coordinate frame is foundational.** Live and recorded must
   register to the same metric space (that's what makes "step out of your body"
   work, even with one rig). The *coordinate-frame* part of calibration is
   MVP-adjacent; only the 4-sensor seam-dissolving **fusion** is later.
2. **Live stream == recorded clip representation.** The renderer should be
   source-agnostic — it plays "point-cloud frames in world space" regardless of
   whether they arrive over the wire (live) or off disk (recorded). Honor this
   from the MVP (record in the wire/take format).

WebXR tightens the **live** path's latency budget (motion-to-photon) → pushes
toward WebRTC/WebTransport over WebSocket eventually; the *prerecorded-in-VR*
path has no such constraint (local playback). Near-term order: **embedded
Kinects streaming + trigger-record → WebXR → remote-location streaming.**

Two repos:
- **`crypt`** — the three.js (r148) creative renderer + rendering R&D (the
  front-end / "how it looks" layer).
- **`crypt-capture`** (this repo) — capture → clean → reconstruct → compress →
  deliver pipeline (the "how the data is made" layer).

## Current status (what's DONE and validated)

- ✅ **Phase 1 spine** (hardware-independent): `protocol/rvl.py` (lossless RVL
  depth codec, ~14×, tested), `protocol/frame.py` (synced depth+color wire
  protocol), `node/sim_node.py` (simulated node), `central/recorder.py` (groups
  sensors by hardware-synced `frame_id`, writes "takes"), `scripts/run_demo.py`.
- ✅ **Real capture validated** on a 1st-gen **Jetson Nano** with one Azure
  Kinect: `node/kinect_node.py` (pyk4a → depth range-clip mask → RVL → stream)
  recorded a real 60-frame take (`takes/real1`, ~131k valid depth px/frame).
  Slow (~1 fps) because RVL is pure-Python on a weak CPU — fine for validation.
- ✅ **Depth-grid meshing**: `node/dump_calibration.py` (depth intrinsics) +
  `processing/mesh_take.py` (unproject depth grid → triangulate with a
  depth-discontinuity edge cut → PLY). Produces a real single-view mesh of the
  subject.
- ✅ **M0 — Fast RVL** (`protocol/rvl.py`): vectorized NumPy `compress`/
  `decompress` alongside the pure-Python reference, **bit-identical** and
  cross-checked in `tests/test_rvl.py`. Public `compress`/`decompress` dispatch
  to NumPy when present, else fall back to pure Python (spine stays dep-free).
  On an x86 dev box: full-res masked frame compress ~68 fps (was ~20), decompress
  ~310 fps (~21×). `kinect_node` now passes the array straight to RVL (no
  per-pixel `.tolist()`). Run: `python3 -m tests.test_rvl`.
- ✅ **M2 (server side) — Live preview relay** (`central/preview_server.py`):
  node TCP `Frame` stream → RVL-decode → unproject to a metric point cloud →
  pixel-stride downsample → broadcast a binary `CPV1` message per frame to
  browser clients over a **WebSocket** (`protocol/websocket.py`, stdlib-only, no
  deps). Geometry only in v0 (no color yet). The browser **viewer lives in the
  separate `crypt` repo** and consumes the documented contract
  (`docs/preview_protocol.md`). Verified end-to-end with **no hardware/browser**
  via `scripts/preview_client.py` (headless WS client): sim node → relay →
  client at ~24.6k pts/frame. `frame.py` was also made Python-3.6-safe (plain
  class, was a `@dataclass` despite the docs — would have broken the Nano).

## The big technical decisions (and WHY) — from a deep-research pass

- **Geometry-based, NOT Gaussian splatting.** We have real metric depth from 4
  Kinects. 3D/4D Gaussian splatting *throws depth away* and re-derives geometry
  via per-scene training (COLMAP + hours of optimization) at video-grade
  bandwidth; web playback of *dynamic* splats is bleeding-edge (one vendor,
  Gracia). Lean into the depth. Revisit 4DGS only as a far-future fidelity bet.
- **Representation = TSDF fusion → watertight mesh per frame ("approach B").**
  Fusing 4 views into one signed-distance field *dissolves seams* by
  construction (this is what Depthkit Studio does internally). Trade-off:
  variable topology per frame → stream as per-frame meshes (not VAT).
  - **Upgrade "approach C":** because the subject is always a HUMAN, fit/track a
    parametric body template (**SMPL-X**) + displacement to get *consistent
    topology* → VAT-able, temporally coherent, tiny. The AI path; more R&D.
- **Keep the per-sensor depth-grid structure.** A depth map has free
  connectivity (connect pixel neighbours, cut on depth discontinuity) and, if
  the grid is constant, fixed topology → VAT (one draw call). The KEY past
  mistake: the old Brekel *point-cloud* export discarded the grid (flat,
  variable-count xyz list). Capture **raw per-sensor depth** (Azure Kinect SDK /
  Open3D), do NOT use fusing exporters (LiveScan3D/Depthkit Studio/Brekel/EF EVE
  all fuse and lose the grid).
- **Cleanup = per-view AI matting** (Robust Video Matting, or BackgroundMattingV2
  with a background plate since the rig is static) beats sparse skeleton
  clipping for clean hair/finger edges. Run it ON the node to cut bandwidth.
  (RVM is GPL-3.0 — licensing flag.)
- **Depth transport = RVL** (Microsoft Research lossless depth codec, designed
  for many Kinects over LAN). **Color = NVENC** H.26x on the node.
- **Web delivery = glTF + `meshopt`, NOT Draco.** Draco is static-geometry-only
  (can't compress morph/animation) and reorders vertices (breaks VAT/morph).
  meshopt preserves order, compresses animation, fast Wasm decode. Vertex color
  now; texture-as-video (UVOL/Arcturus style) later for photoreal. Mesh-sequence
  web playback is production-ready (Arcturus HoloSuite, UVOL); your VAT renderer
  is the DIY version.
- **Distributed capture.** One Kinect per edge node (4 Kinects on one PC
  saturates USB3 controllers). HW frame-sync via the 3.5 mm daisy-chain between
  cameras; central machine sends the trigger (arm/record/stop) and does
  alignment + fusion. Each node also does the AI matting (distributes the ML,
  streams only the masked foreground).
- **Node hardware.** Azure Kinect SDK is archived + x86-first; **Body Tracking
  does NOT run on ARM/Jetson**; new Jetson OS (Orin/JetPack 5-6) fights the old
  depth-engine binary. **Lowest-risk node = x86 mini-PC + small NVIDIA GPU.**
  The Jetson Nano works (proven JetPack 4 / Ubuntu 18.04 combo) and is great for
  free validation but too weak for production matting. (If Jetson: Orin NX, not
  Orin Nano — Nano has no NVENC. Azure Kinect itself is discontinued; Orbbec
  Femto Bolt is the successor.)

## Repo layout

```
protocol/   rvl.py (depth codec), frame.py (wire protocol), websocket.py (ws relay)
node/       sim_node.py, kinect_node.py (real), dump_calibration.py
central/    recorder.py (records synced takes), preview_server.py (live ws relay)
processing/ mesh_take.py (take -> depth-grid PLY mesh)
scripts/    run_demo.py (hardware-free spine demo), preview_client.py (headless ws test)
tests/      test_rvl.py
docs/       hardware.md, protocol.md, preview_protocol.md, realtime_architecture.md, jetson_setup.md
takes/      recordings (gitignored)
```
The browser **viewer is NOT here** — it lives in the `crypt` repo and consumes
`docs/preview_protocol.md`. The Jetson pulls this repo and runs only `node/` +
`protocol/`; it never runs the central server or the viewer.

## How to run

```bash
# Hardware-free spine test:
python3 scripts/run_demo.py --sensors 4 --frames 15

# Codec tests (numpy fast path == pure-Python reference, bit-identical):
python3 -m tests.test_rvl

# Live preview, no hardware/browser (3 terminals): relay, sim node, headless client:
python3 -m central.preview_server --stride 2
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 300
python3 -m scripts.preview_client --frames 30
# (real browser viewer = the `crypt` repo; speaks docs/preview_protocol.md)

# Real single-sensor capture (recorder + node, localhost):
python3 -m central.recorder --port 9000 --sensors 1 --out takes/real1
python3 -m node.kinect_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 60

# Mesh a recorded take:
python3 -m node.dump_calibration --out takes/real1/calib.json
python3 -m processing.mesh_take --take takes/real1 --calib takes/real1/calib.json --frame 0
# -> takes/real1/mesh/frame_000000.ply  (tune --edge-mm: lower=less webbing, higher=fewer holes)
```

## Environment gotchas (learned the hard way on the Nano)

- **Source-built Azure Kinect SDK** installs incompletely: copy the *generated*
  headers `k4aversion.h` + `k4a_export.h` and the `k4arecord/*` headers into
  `/usr/include/k4a*`, and copy `libk4arecord.so*` into
  `/usr/lib/aarch64-linux-gnu/` (+`ldconfig`). Set `K4A_INCLUDE_DIR=/usr/include`
  `K4A_LIB_DIR=/usr/lib/aarch64-linux-gnu`. Install pyk4a with `--no-deps`
  (system numpy via apt) then `pip install --user typing_extensions`.
- **Python 3.6** (Nano default): `frame.py` is a plain class (no dataclasses) so
  it imports on 3.6; the codebase avoids `time.time_ns()` (3.7+) — uses
  `int(time.time()*1e9)` (node files included). Keep new node/protocol code
  3.6-safe. (`central/preview_server.py` + `protocol/websocket.py` are
  central-only, x86/3.8+ — they don't run on the Nano.)
- **Jetson USB**: `sudo sh -c 'echo 256 > /sys/module/usbcore/parameters/usbfs_memory_mb'`
  to stop `libusb errno=12` transfer errors; each Kinect needs its own 5V supply.
- See `docs/jetson_setup.md`.

## Rendering R&D already done (in the `crypt` repo)

Prototyped many ways to render the capture: GL_POINTS sphere-impostors + Eye-
Dome Lighting; Vertex Animation Textures (one-draw-call); per-point PCA-normal
surfel splatting; EWA weighted-splat blending; per-frame Delaunay trimeshes with
interpolated vertex colors. Key learnings: a **fixed-topology depth-grid mesh +
VAT** is the scalable representation; flat per-splat color reads as "tiled
cells" on a solid surface (fixed by EWA blending or a real interpolated mesh);
and the capture's **per-point colors are high quality** (not compressed — that
was a rendering artifact, not the data). Branches: `…-edl`, `…-vat`, `…-mesh`,
`…-deferred`, `…-surfel`, `…-ewa`, `…-trimesh`.

## Roadmap / next steps

Reoriented around the real-time app (full plan in
`docs/realtime_architecture.md`). MVP = **one camera**, live preview +
trigger-record-download.

1. ✅ **M0 — Fast RVL.** Vectorized NumPy `compress`/`decompress`, bit-identical
   to the pure-Python reference with a fallback. Done (see Current status).
2. 🟡 **M2 — Live preview.** ✅ *server side*: `central/preview_server.py` relays
   node frames → `CPV1` point clouds over WebSocket; verified headless with
   `scripts/preview_client.py`. ⏳ *remaining*: the browser three.js/WebXR viewer
   in the **`crypt` repo** (consumes `docs/preview_protocol.md`); optional color.
3. **M1 — Control plane.** Central ↔ node command channel (`arm/record/stop/
   status`); node grows a control listener. **Next concrete task** (M3 needs it).
4. **M3 — Record + download.** Trigger → node records full-rate to **local
   disk** → HTTP file server → recordings browser + download in UI.
5. **M4 — N nodes.** Node discovery/registry; trigger fans out to all.
6. **Phase 2 (M5) — Aligned/fused.** Extrinsic calibration (marker + ICP) →
   aligned multi-view cloud; **TSDF fusion** (Open3D) → watertight mesh →
   glTF/meshopt export for the renderer.
7. **Phase 3 — creative FX** (particles from capture geometry); SMPL-X template
   tracking (approach C) for fixed-topology streamable compression.

Deferred (still wanted, off the MVP critical path): **aligned color → colored
mesh** (node captures `pyk4a` `transformed_color` 640×576 so `mesh_take.py` can
bake per-vertex colors); NVENC color on node; Web Worker / Wasm RVL for
browser-side preview decode.

## Open items

- Confirm whether any Brekel export retains structured per-sensor depth (its
  site blocks automated checks) before committing to a fully custom capture.
- RVM GPL-3.0 licensing vs a permissive matting model (BGMv2/MediaPipe/SAM2).
