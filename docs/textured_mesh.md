# Textured mesh — full-resolution colour on cheap geometry

**Problem.** Facial colour detail is soft because colour is carried **per point**,
so colour resolution is bolted to geometry resolution. The two existing levers
both fail: `color_to_depth` caps colour at the depth grid (~640×576), and
`depth_to_color` lifts colour by exploding the point count to the colour grid
(~2.5×) — which is heavy at *every* stage (node CPU, relay CPU, wire, GPU) and
tested as unusable close-up.

**Fix (this doc).** Decouple colour from geometry, the standard volumetric-video
approach (Arcturus/UVOL/Depthkit). Keep **geometry at depth resolution**
(`color_to_depth`, cheap, 30 fps) and carry colour as **one full-resolution JPEG
per frame + per-vertex UVs**. The viewer's `MeshCloud` samples that texture
**per fragment**, interpolated across triangles → colour detail at the colour
camera's full resolution on a ~640×576 mesh. Point count, RVL, and render load
stay at the cheap `color_to_depth` level.

This only benefits the **mesh** render (a point is one fragment, so per-point
colour is already all a point can show). That's the right target — the mesh is
already the better face render.

## Where each piece runs

```
node (color_to_depth)                relay                         viewer (MeshCloud)
 depth grid ─RVL─────────────────►  unproject → 3D (depth optical)
 cap.color (full res) ─JPEG─────►   forward JPEG ───────────────►  decode → THREE texture
 colour intrinsics+dist+extr ──►    project 3D → colour UV ─────►  UV attribute → sample map
```

- **Geometry**: unchanged `color_to_depth` depth grid (points + the CPV grid
  block that already drives meshing).
- **UVs computed at the RELAY** (x86, already unprojects to 3D). It projects each
  depth point into the colour camera using colour intrinsics + Brown-Conrady
  distortion + the DEPTH→COLOR extrinsic — all sent once by the node. The Jetson
  stays out of the per-point UV maths (it is CPU-bound).
- **Colour transport = JPEG per frame**, encoded on the node **on a dedicated
  encoder THREAD** (`cv2`/libjpeg-turbo releases the GIL), forwarded verbatim by
  the relay. Codec byte in the wire so NVENC/H.26x can slot in later.
  **Hard-won (2026-07-06):** encoding inline on the CAPTURE thread halved node
  fps (30→15) — the ~15-25 ms encode blocked every capture. It now runs on its
  own thread; the capture thread only copies the latest colour into a slot
  (latest-wins) and moves on, so encode overlaps capture on another core.
- Per-point **rgb is still sent** (additive): the point render and older viewers
  keep working unchanged; textured mesh is a pure add. (Dropping rgb in textured
  mode is a later bandwidth optimisation.)

## Node → relay protocol additions (`protocol/frame.py`)

Two new messages, dispatched by `read_message` (additive; a node only emits them
when textured mode is enabled, which only a matching viewer requests):

- **`CCLR`** — colour-camera calibration, sent once per (re)connect/reconfig
  alongside `CCAL`: `sensor_id`, colour `w,h`, `fx,fy,cx,cy`, 8 Brown-Conrady
  coeffs (`k1,k2,p1,p2,k3,k4,k5,k6`, OpenCV order), and the **DEPTH→COLOR**
  rigid extrinsic `R (9, row-major)`, `t (3, metres)` (`P_color = R·P_depth+t`).
- **`CTEX`** — one JPEG colour image for a frame, sent **immediately before** its
  `Frame`: `sensor_id`, `frame_id`, `format` (0 = JPEG), `w,h`, `len`, bytes. The
  relay stashes the latest texture per sensor and attaches it to that sensor's
  next frame.

`set_texture {enabled}` control command turns node JPEG encoding + `CTEX`/`CCLR`
on/off live (off by default → zero cost until a viewer in mesh mode asks).

## Relay → viewer wire additions (`CPV1`, additive blocks)

Two new flag bits + trailing blocks, **after** the existing grid block so older
viewers ignore them:

- **`FLAG_UV = 0x10`** — `count × 2 × uint16`, normalised UV × 65535 (`u,v` in
  `[0,1]`, texture origin top-left), one pair per point (same order as
  positions). Off-image points clamp to `[0,1]`.
- **`FLAG_TEXTURE = 0x20`** (last block) — `u8 format` (0 = JPEG), `u16 tex_w`,
  `u16 tex_h`, `u32 len`, then `len` bytes. One image per frame, shared by all
  the frame's UVs.

Full byte layout lives in `docs/preview_protocol.md`. CPV2 carries them
identically (UV is already compact; the JPEG is already compressed).

## Viewer (`crypt`): `MeshCloud` texturing

- Decode the JPEG (`createImageBitmap`) → a `THREE.Texture` (updated per frame,
  like a video texture; `colorSpace = SRGBColorSpace`, `flipY` per UV origin).
- Add a `uv` attribute (preallocated, filled from the UV block) and set the
  mesh material's `map` = the texture. Vertex colours become a tint (white)
  or are dropped; the texture is the albedo.
- Fall back to the existing per-vertex-colour path when no texture/UV block is
  present (old relay, or texture disabled).

## Why not just make `depth_to_color`/CPV2 fast enough?

CPV2 halves the *wire* but not the point count, so `depth_to_color` stays heavy
on node/relay/GPU. Textured mesh removes the reason to inflate geometry at all —
full colour on depth-res geometry — so it is the actual fix, and it composes with
CPV2 (smaller positions/grid) and background subtraction (fewer points) on top.

## Subject crop + downscale (mesh-perf fix, 2026-07-14)

The first cut shipped the **whole colour image** as JPEG every frame. That made
the mesh far heavier than the point/splat renders on EVERY axis at once — node
encode (~15-25 ms of a 2-4 MP image), the node→relay + relay→browser wire
(~150-400 KB/frame ≈ 36-96 Mbps for ONE camera's texture, on top of geometry),
and the browser `createImageBitmap` of a multi-MP JPEG per frame. Points/splats
ship only the subject-proportional per-point rgb, so they stay light; the texture
was whole-frame. On a choked link the texture stream (a separate fid-paired
stream) fell seconds behind, then the geometry frame it's embedded in blocked
mid-send → the "texture drifts as I move / freezes for a few seconds" symptom.

Fix: make the texture **subject-proportional like the geometry** —
- **Node crops** `cap.color` to the subject's **colour-space bounding box**
  (`_subject_color_bbox`: subsample the subtracted foreground, pinhole-unproject,
  DEPTH→COLOR extrinsic, colour intrinsics + Brown-Conrady forward — the same
  math as the relay's `_project_color_uv`, on the CAPTURE thread reusing the
  cached depth + colour calib; approximate + padded 8 %, so it only has to
  CONTAIN the subject, not be pixel-exact), then **downscales** the crop's
  longest edge to `--texture-max-dim` (default 960). ~8-12× fewer bytes; the
  colour is still sampled per-fragment so it stays well above the depth-grid
  resolution.
- The crop rect `(u0,v0,u1,v1)` (normalised full-image coords) rides in `CTEX`
  (node→relay) and the CPV `texture` block (relay→viewer).
- **CPU-mesh path:** the relay **remaps** its full-image UVs into the crop
  (`_remap_uv_to_crop`) before the `uv` block, so `MeshCloud` samples the cropped
  image with no change.
- **GPU-mesh path (cpv3):** no relay UV block, so `GpuMeshCloud` computes the
  full-image colour-UV in its vertex shader and remaps it with a `uColorCrop`
  uniform set from the texture block's crop rect.

`(0,0,1,1)` (no plate / no subject / no calib) = the whole frame = the old
behaviour. Node-side change → push→service-restart to deploy; relay + viewer
restart on the central/browser side. Tests: `tests/test_texture_crop.py`
(bbox containment + downscale), `test_texture.test_uv_crop_remap` (relay remap),
CTEX/CPV crop round-trip in `test_texture`.

## Status / rollout

Built in stages: (1) wire protocol + relay UV projection + `sim_node` synthetic
texture + headless test; (2) `kinect_node` real JPEG path + `set_texture`; (3)
viewer `MeshCloud` texturing; (4) subject crop + downscale (above). Off by
default end-to-end — a viewer opts in when the subject render is `mesh`.
