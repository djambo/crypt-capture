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
- **Colour transport = JPEG per frame**, encoded on the node (Orin has hardware
  JPEG; `cv2`/`Pillow` fallback), forwarded verbatim by the relay. Codec byte in
  the wire so NVENC/H.26x can slot in later behind the same block.
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

## Status / rollout

Built in stages: (1) wire protocol + relay UV projection + `sim_node` synthetic
texture + headless test; (2) `kinect_node` real JPEG path + `set_texture`; (3)
viewer `MeshCloud` texturing. Off by default end-to-end — a viewer opts in when
the subject render is `mesh`.
