# Browser-GPU unprojection ("approach A") — design

The relay currently unprojects depth to XYZ floats on the CPU and ships those.
The data's destination is a GPU renderer, so this is backwards: **ship compact
depth + calibration, unproject on the client GPU.** This is the standard for
live depth-camera streaming, and it fixes ingest CPU *and* transport at once.

## Two variants — we are building A-lite

- **A-lite (this plan):** the relay still does RVL-decode + denoise on the CPU
  (ordered, per-sensor, stateful — where it belongs), but instead of expanding
  to XYZ it ships **per-valid-pixel depth + the grid bitmap + calibration
  uniforms**. The **browser** unprojects, projects UVs, applies the rig
  transform, and meshes — all on the GPU. No Wasm RVL, no shader denoise needed.
- **Full-A (later, if the relay CPU ever binds on full clouds):** relay just
  forwards RVL; the browser decodes RVL (Wasm) and denoises (shader passes) too.
  Only extra benefit over A-lite is offloading decode+denoise, which are cheap
  for a subtracted subject (and now ~10× faster on the relay via the Numba RVL
  decoder). Not worth the browser complexity yet.

## Why A-lite wins

- **Wire ~9× smaller.** Today (CPV1) a valid point is ~19 B (xyz 12 + rgb 3 +
  grid 4 + uv). A-lite sends **depth (2 B) + grid bitmap (~0.13 B)** ≈ **2.1 B/pt**;
  everything else (intrinsics, distortion, rig matrix, colour calib) is a handful
  of per-frame *uniforms*, and the colour is the shared JPEG we already send.
  A subtracted subject drops from ~114 Mbit/s to ~13 Mbit/s; a full 720p cloud
  from ~4 Gbit/s to ~450 Mbit/s (fits a gigabit link **uncompressed-XYZ-free**).
- **Relay CPU drops hard.** It stops doing per-point unproject, UV projection,
  and XYZ serialization — the exact stages the `--workers` pool exists to spread.
  What remains (RVL decode [Numba], denoise, repackage) is the small ordered
  core.
- **GPU does what it's for.** Unprojecting a depth map (ray × depth) + building a
  grid mesh + sampling a texture is trivial, high-throughput GPU work; a mid
  gaming PC eats it. The renderer already holds the texture and draws the mesh.
- **Points survive.** The point render just unprojects to `gl_Points` and samples
  the texture at each point's projected UV for colour — no per-point rgb needed.

## Wire format (new magic `CPV3`, additive; CPV1/CPV2 stay)

Per frame, per sensor:
- **header** — same 20-byte header, `magic = "CPV3"`, `count` = valid pixels.
- **depth block** — `count × uint16` depth (mm), one per valid pixel in grid
  row-major order (optionally CPV2-style quantised if we want <2 B).
- **grid block** — the CPV2 valid-mask **bitmap** (`u16 w,h` + `ceil(w*h/8)`
  bits). Set bits in order are 1:1 with the depth values, and give each point its
  `(u,v)` → the browser recovers the ray and the mesh connectivity from this
  alone.
- **texture block** — the shared JPEG (unchanged from the textured-mesh work).

Per-(re)connect / on-change **calibration message(s)** (JSON or a small binary
block; low-rate): depth intrinsics + Brown-Conrady distortion (or a baked
**ray-table texture** the browser samples), the colour intrinsics + DEPTH→COLOR
extrinsic (for in-shader UV projection), and the per-sensor **rig matrix** (view
→ world). The relay already has all of these.

## Browser (crypt) shader plan

- Upload the depth block as a **data texture** (R16UI) sized to the sub-grid, and
  the bitmap → an index/vertex list (same as MeshCloud does now from the grid).
- **Vertex shader**: for vertex at grid `(u,v)`, read `depth`, look up the
  undistorted ray `(rx,ry)` (ray-table texture, or compute from intrinsics),
  `pos_optical = vec3(rx*d, ry*d, d)`, flip to view space, then `pos_world =
  rigMatrix * pos`. Compute the colour UV by projecting `pos` through the colour
  intrinsics+extrinsic uniforms (the same forward Brown-Conrady the relay does
  today in `_project_color_uv`).
- **Fragment shader**: sample the JPEG texture at the UV (mesh) — identical look
  to today's textured mesh, just computed client-side.
- Mesh connectivity: the existing `MeshCloud` triangulation, fed by the bitmap.
- Denoise (temporal/spatial) stays on the relay for A-lite; can become a
  ping-pong depth-texture pass later (full-A).

## Tiers: PCVR vs standalone — one architecture, per-client LOD

Standalone headsets (Quest 3, Pico) are the reason to do A-lite, **not** a reason
to fear it. Unprojecting a depth map on the GPU is trivially cheap even on a
mobile Adreno; what actually binds standalone is (1) **Wi-Fi bandwidth**, (2)
**mobile-CPU JS parse**, (3) **stereo-90 Hz fill rate**. A-lite *reduces* (1) and
(2) directly (~9× smaller wire, no XYZ-float handling), and the CPU-unproject
alternative would be far worse on a mobile CPU. So the tier difference is
**payload level-of-detail, negotiated per client — NOT a second architecture.**
Keep one A-lite pipeline (source-agnostic, recorded == live) and vary the data:

- **Geometry density** — coarser grid stride ⇒ fewer points/vertices (the grid
  block already carries the effective stride).
- **Texture codec + resolution** — the texture block's `format` byte is the tier
  hinge: **PCVR → JPEG; standalone → H.264/H.265 via WebCodecs** (Quest decodes
  video in a dedicated HW block, far cheaper than per-frame JPEG), at a smaller
  resolution.
- **fps cap** and **subject-only** (background subtraction) for standalone.
- **Render**: the **textured mesh** is the standalone-friendly render (a
  ~25k-vertex mesh + one texture draws easily at stereo 90 Hz — cheaper than
  millions of points); add foveated rendering. Same shader as PCVR.

Negotiated at connect (the relay already gives each viewer its own sender), so a
Quest and a gaming PC watch the *same scene* at different densities — no forked
render path. Needs on-device measurement (Quest browser WebGL2 perf, WebCodecs
decode cost, IR coexistence with the Kinect) before the tier numbers are trusted.

## What stays on the relay

RVL decode (Numba, ~10× faster now), temporal/spatial denoise, background
subtraction, rig solve, calibration, recording tee. It becomes a
**decode → clean → repackage → forward** pipe, not a per-point compute engine.

## Migration / compatibility

Additive: `--wire cpv3` opt-in, default stays `cpv1`. The viewer dispatches on
the magic (it already does for CPV1/CPV2). CPV1/CPV2 remain for old viewers and
for the recording format until the viewer's CPV3 GPU path is proven. Recordings
under CPV3 replay through the same GPU path.

## Effort / risks / open questions

- **Effort:** moderate-large. Relay: a CPV3 encoder (mostly *removing* work) +
  ship the calib uniforms. Viewer: the unproject shader + ray-table/intrinsics
  handling — the real work, but well-trodden GPU territory.
- **Precision:** uint16 mm depth is the sensor's native precision (lossless);
  the quantised-XYZ concern doesn't arise (we quantise nothing new).
- **Undistortion in-shader:** either bake a ray-table texture on the relay (send
  once per resolution — simplest, exact) or do the iterative undistort in the
  shader. Ray-table texture is recommended (the relay already builds it).
- **WebGL2 vs WebGPU:** WebGL2 (R16UI data textures, vertex-texture fetch) is
  enough and is what three.js r185 uses today. WebGPU is a later upgrade.
- Pairs naturally with **WebTransport/WebRTC** (approach D) for the remote-
  location step — smaller frames make that far easier.

## Sequencing

1. ✅ **Numba RVL decode** (approach C) — done; ~10× relay decode on full clouds,
   and it stays on A-lite's critical path (the relay still decodes).
2. **CPV3 relay encoder + calib uniforms** (this doc).
3. **Viewer GPU unproject shader** (MeshCloud/PointCloud CPV3 path).
4. Later: WebTransport transport (D), then full-A (Wasm RVL + shader denoise) only
   if the relay CPU is measured to bind.
