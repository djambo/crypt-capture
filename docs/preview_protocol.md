# Live preview protocol (central → browser)

The contract between **crypt-capture** (producer: `central/preview_server.py`)
and the **crypt** viewer (consumer, separate repo). The server relays a
downsampled point cloud per captured frame to every connected browser over a
**WebSocket**; the viewer renders it. Keeping this written down is what lets the
two repos evolve independently — change the wire format here, bump the version,
update the viewer.

This is the *live* path. The recorded-take format (`docs/protocol.md`) is the
*offline* one; both describe "point-cloud frames in world space" so the renderer
can stay source-agnostic (see the North Star in `CLAUDE.md`).

## Transport

- Plain WebSocket (RFC 6455), binary messages, one message = one preview frame.
- Default endpoint: `ws://<central>:8080/`.
- v0 is **best-effort over a reliable socket** (WebSocket = TCP). If a client is
  slow, the server drops frames to it rather than buffering unbounded. Lower
  latency transports (WebRTC/WebTransport) are a later swap; the *message body*
  below is transport-independent.

## Message: `CPV1` (PreviewFrame), little-endian

| field | type | meaning |
|---|---|---|
| magic | `4s` | `CPV1` |
| flags | `u32` | bit0 = positions present (always 1); bit1 = `rgb` present; bit2 = `gravity` present; bit3 = `grid` present; bit4 = `uv` present; bit5 = `texture` present |
| sensor_id | `u32` | source sensor (0..N-1) |
| frame_id | `u32` | capture frame index (low 32 bits) |
| count | `u32` | number of points |

Then the payload blocks, in order:

1. **positions** — `count × 3 × float32`, metres, in view/world space
   (`x` right, `y` up, `z` toward viewer i.e. camera looks down −z). Ready to
   drop into a three.js `Float32Array` position attribute.
2. **rgb** *(only if flag bit1 set)* — `count × 3 × uint8`, 0–255 per channel,
   one triple per point (same order as positions). Sent when the node provides
   depth-aligned color (`kinect_node` via `transformed_color`; `sim_node`
   always). The relay sets bit1 whenever it has color for the frame; a viewer
   must still handle bit1 = 0 (geometry only) gracefully.
3. **gravity** *(only if flag bit2 set)* — `3 × float32`, a **gravity (down)
   unit vector** in the same view/world frame as positions, derived from the
   sensor's IMU accelerometer. It gives the cloud an initial orientation (which
   way is down / where the floor lies) before any extrinsic calibration. Static-
   ish (the rig doesn't move) but attached to every frame so a late-joining
   viewer always has it. **Read it with a `DataView` (`getFloat32`), not a
   `Float32Array` view:** when rgb is present this block starts at a non-4-byte-
   aligned offset and a typed-array view would throw.
4. **grid** *(only if flag bit3 set)* — `u16 grid_w`, `u16 grid_h`, then
   `count × uint32`: per point, its **row-major linear index** (`v*grid_w + u`)
   into the (strided) depth sub-grid it was sampled from. This is the
   depth-map connectivity the flat point list otherwise loses — from it the
   viewer can re-mesh neighbouring grid pixels into triangles (the **textured
   mesh** subject render; cut edges on depth discontinuities). Indices are
   ascending and pair 1:1 with positions (index *k* describes point *k*). The
   dims ride on every frame because `set_camera` resizes the grid live. Sent
   by default; `preview_server --no-grid` drops it (−4 bytes/point). Same
   alignment caveat as gravity: with rgb present the offset is not 4-aligned —
   copy the bytes before viewing as `Uint32Array`.

5. **uv** *(only if flag bit4 set)* — `count × 2 × uint16`, each `u,v` normalised
   to `[0,1]` (`× 65535`, texture top-left origin), one pair per point (same
   order as positions). The **textured mesh** (docs/textured_mesh.md): the relay
   projects each depth point into the colour image, so the viewer can sample the
   full-resolution colour texture per fragment instead of using the depth-res
   per-vertex `rgb`. After the grid block.
6. **texture** *(only if flag bit5 set, LAST block)* — `u8 format` (0 = JPEG),
   `u16 tex_w`, `u16 tex_h`, `u32 len`, then `len` bytes of the encoded colour
   image. **One image per frame**, shared by all the frame's UVs. Decode it
   (`createImageBitmap`) into a texture and set it as the mesh albedo; drop
   vertex colours while textured (the texture is the colour). These two blocks
   are emitted only while a viewer has requested `set_texture` (mesh render).

Only valid (non-zero-depth) points are sent, after a stride-based downsample —
so `count` varies per frame. The `--max-points` cap (default **0 = uncapped**,
full resolution) is enforced by **growing the stride** (never by dropping
individual points, which would punch periodic holes into the grid and break
the mesh triangulation) — so a capped frame arrives as a coarser but still
fully-connected grid, with `grid_w`/`grid_h` reflecting the effective stride. The viewer must read `count` from the header, not
assume a fixed size. The `rgb` block, when present, starts at byte `20 +
count*12`; the `gravity` block starts right after it (`20 + count*12`, plus
`count*3` when rgb is present); the `grid` block is last (add 12 more when
gravity is present).

## Message: `CPV2` (compact wire format, little-endian)

Selected with `preview_server --wire cpv2` (default is `cpv1`). It carries the
**same cloud** as `CPV1` — same header, same flag bits, same block order — but
positions are **uint16-quantised** and the grid is a **valid-mask bitmap**, so a
full-resolution frame is ~52 % smaller on the wire (19 B/pt → ~9 B/pt). The
MAGIC (`CPV2` vs `CPV1`) is what tells the viewer how to read positions and the
grid block; a viewer must dispatch on it. Everything else (control plane, JSON,
recording) is unchanged.

Header: identical 20-byte layout, `magic = "CPV2"`. Then, in order:

0. **quant** *(always present, right after the header)* — `offset_x, offset_y,
   offset_z, scale` as `4 × float32` (16 bytes). Dequantise every position as
   `p = q * scale + offset`. `offset` is the frame's min corner and `scale` is a
   single uniform metres-per-step = `max_span / 65535`, recomputed per frame.
1. **positions** — `count × 3 × uint16` (little-endian). Dequantise with the
   quant block above → the identical view/world-space metres `CPV1` sends.
   **Why this is lossless in practice:** the step is `max_span/65535` ≈ **0.03 mm**
   for a ~2 m subject, **0.12 mm** for an ~8 m room — 30–60× below the Azure
   Kinect's random ToF noise (~2–4 mm best case, worse at range), i.e. far below
   the jitter the relay's temporal filter is already smoothing. It shrinks bytes
   below the noise floor, it does not throw away real depth resolution.
2. **rgb** *(flag bit1)* — `count × 3 × uint8`. **Unchanged from CPV1.**
3. **gravity** *(flag bit2)* — `3 × float32`. **Unchanged from CPV1.**
   The **uv** (bit4) and **texture** (bit5) blocks, when present, are also
   **byte-identical to CPV1** and come last, in that order (only positions and
   the grid block differ between the formats).
4. **grid** *(flag bit3, last block)* — `u16 grid_w`, `u16 grid_h`, then a
   **bitmap** of `ceil(grid_w*grid_h/8)` bytes, **LSB-first**: bit
   `i = v*grid_w + u` (byte `i>>3`, bit `i&7`) is set when that sub-grid cell
   carried a point. The set bits **in ascending order are 1:1 with the
   positions**, so the viewer rebuilds the exact CPV1 index list by scanning
   them (`indices = flatnonzero(unpackbits(bytes, bitorder="little"))`) and
   meshes exactly as before. This replaces CPV1's `count × u32` indices: on a
   full frame the bitmap is ~0.13 B/pt vs 4 B/pt.

Offsets are all 2-byte-aligned at most, so (as with CPV1) read multi-byte
blocks with a `DataView` / copy before viewing as a typed array. `count = 0`
frames carry the quant block (scale = 1) and no position bytes.

## Message: `CPV3` (browser-GPU unproject, little-endian)

Selected with `preview_server --wire cpv3` (default `cpv1`). The relay ships
**depth + per-point rgb + a valid-mask bitmap + per-sensor calibration** and the
**browser unprojects on the GPU** (approach A, `docs/gpu_unproject.md`) — positions
(~2 B/pt) and UV are derived in-shader from depth + the `sensor_calib` message
(below), but **rgb IS on the wire** so the point render is coloured frame-locked
(no texture needed for points — see the rgb block). Dispatch on the 4-byte MAGIC.

Header: identical 20-byte layout, `magic = "CPV3"`, `count` = valid pixels,
`flags` bit0 (positions, implied) + bit3 (grid, always) + bit1 (rgb) + bit2
(gravity) + bit5 (texture) as applicable. Then, in order (mirroring CPV1's
positions→rgb→gravity→grid→texture so the browser parser is uniform):

0. **step** *(always, right after the header)* — `u16 step_u`, `u16 step_v`: the
   full-resolution pixels per grid cell (usually 1; >1 when the relay coarsened
   for `--max-points`). Grid cell `(u,v)` maps to full-res pixel
   `(u*step_u, v*step_v)` — the browser uses this to look up that cell's ray.
1. **depth** — `count × uint16` (mm), one per valid pixel, grid row-major (same
   order as the set bits below).
2. **rgb** *(flag bit1)* — `count × 3 × uint8`, same order as depth, one per
   valid pixel. The POINT render's colour (frame-locked to the geometry, exactly
   like CPV1/CPV2) — added 2026-07-07 to fix "colour lags depth" on the GPU point
   path (the texture is an async-decoded separate stream; per-point rgb is not).
   The texture stays for the MESH only. Byte-identical to CPV1's rgb block.
3. **gravity** *(flag bit2)* — `3 × float32`, view-frame down (the browser
   applies the rig, so this is pre-rig; unchanged bytes from CPV1).
4. **grid** *(flag bit3, always)* — `u16 grid_w`, `u16 grid_h`, then the
   valid-mask **bitmap** (`ceil(grid_w*grid_h/8)` bytes, LSB-first) — identical
   to CPV2's grid. Set bits in order are 1:1 with the depth/rgb values.
5. **texture** *(flag bit5, last)* — the JPEG colour image, byte-identical to the
   textured-mesh block (`u8 format`, `u16 w`, `u16 h`, `u32 len`, bytes). Used by
   the MESH render; the POINT render uses the rgb block instead.

The browser reconstructs each point: `u,v` from the bitmap → full-res pixel via
`step` → undistorted ray (from `sensor_calib.depth`) → `pos = ray * depth` →
view flip → `pos_world = rig * pos` (rig from `rig_poses`); colour is the per-point
rgb (points) or the texture sampled at the in-shader UV from `sensor_calib.color`
(mesh). Geometry is **lossless** — it reconstructs the exact XYZ CPV1 emits
(`tests/test_cpv3.py`).

### `sensor_calib` (server → browser JSON, for CPV3)

Sent to each client on connect and whenever the depth/colour intrinsics arrive or
change. Everything the GPU needs to unproject:

```json
{"type":"sensor_calib","sensor":0,
 "depth":{"fx","fy","cx","cy","dist":[8],"w","h"},
 "rig":{"R":[9],"t":[3]}|null,
 "color":{"fx","fy","cx","cy","dist":[8],"w","h","R":[9],"t":[3]}|null}
```

`depth` = full-res depth intrinsics + Brown-Conrady distortion (build the ray
table). `rig` = view→world (also delivered live via `rig_poses`; null = identity).
`color` = colour intrinsics + DEPTH→COLOR extrinsic for in-shader UV projection
(null until the node sends colour calib). Unknown to CPV1/CPV2 viewers — ignored.

## Viewer side (sketch, lives in `crypt`)

```js
ws.binaryType = "arraybuffer";
ws.onmessage = (e) => {
  const dv = new DataView(e.data);
  // magic @0..3 === "CPV1"; flags @4; sensor @8; frame @12; count @16
  const count = dv.getUint32(16, true);
  const positions = new Float32Array(e.data, 20, count * 3);
  // geometry.setAttribute('position', new THREE.BufferAttribute(positions, 3))
};
```

## Upstream: browser → server commands (control plane)

The same WebSocket also carries **control commands the other way** (viewer →
server → node). Send a WebSocket **text** message containing a JSON command; the
server forwards whitelisted commands down to the capture node(s), which apply
them live. This is low-rate and independent of the frame stream, so it doesn't
affect streaming performance.

Commands are `{"cmd": ...}` objects. Current commands:

| command | meaning |
|---|---|
| `{"cmd":"capture_bg","frames":<n>}` / `{"cmd":"clear_bg"}` / `{"cmd":"set_bg_margin","mm":<n>}` | background-plate subtraction (snapshot the empty scene, then stream only the subject). `set_bg_margin` is the threshold — how much closer than the plate a point must be to be kept. |
| `{"cmd":"set_denoise","min_neighbors":<n>}` | speckle filter strength (0 = off). |
| `{"cmd":"set_erode","px":<n>}` | **silhouette-rim trim** (node default **1**; 0 = off), applied only while background subtraction is active. Erodes the foreground mask by `px` pixels: at every depth edge the ToF sensor returns 1–2 px of mixed pixels whose depth lands between subject and wall (so they pass plate subtraction) and whose colour is sampled from the wall behind — the wall-coloured fringe outlining the subject. Raising it trims deeper but starts eating fine silhouette detail (fingers) above ~2. Near-zero cost (boolean AND passes on the mask) and it *shrinks* the point count/wire. |
| `{"cmd":"set_camera", "depth_mode":<m>, "color_resolution":<r>, "fps":<f>, "align":<a>}` | **pick which Kinect data to send** (all fields optional; unknown/unchanged ignored). See below. |
| `{"cmd":"set_imu","enabled":<bool>}` | **stream live IMU orientation.** When enabled, the node re-reads the accelerometer every ~10 frames and re-sends a fresh gravity (down) vector, so the cloud reorients live as the camera is physically turned. Off by default (one gravity vector is still sent at connect). The gravity rides in the `CPV1` gravity block (bit2). |
| `{"cmd":"set_texture","enabled":<bool>,"quality":<1-100>}` | **textured mesh** (docs/textured_mesh.md). When enabled (the viewer sends it while the subject render is `mesh`, `color_to_depth` only), the node ships the full-resolution colour image as JPEG + colour calibration, and the relay adds the `uv` (bit4) + `texture` (bit5) blocks to each `CPV1` frame so the mesh is textured at the colour camera's full resolution on cheap depth-res geometry. Off by default (zero cost in point mode). |
| `{"cmd":"set_ir","enabled":<bool>}` | **IR colour mode.** When enabled, the node substitutes tone-mapped **active-IR grey** for the camera colour in the per-point payload. The white point is **auto-gained**: each frame's 99th-percentile IR value, EMA-smoothed across frames (active-IR brightness spans orders of magnitude with distance/reflectivity, so any fixed full-scale either saturates the subject white or crushes it black), with a sqrt curve on top for shadow detail and a floor so an empty scene's noise isn't amplified. The IR image shares the depth camera's geometry (same grid, same valid mask), so the swap is exact per point and the wire format is UNCHANGED — the `rgb` block just carries grey (R==G==B), and cpv1/cpv2/cpv3, recordings and every viewer render follow automatically. Off by default. The viewer stops requesting `set_texture` while IR is on (the JPEG texture stays RGB and would mismatch the IR points). `depth_to_color` needs a pyk4a with `transformed_ir` (else the node logs once and stays on camera colour). |
| `{"cmd":"node_admin","sensor":<id>,"action":"restart"\|"reboot"}` | **per-node remote admin** (2026-07-11, the viewer's per-camera ⚙ button). Broadcast to every node like the other forwarded commands; each node ignores a non-matching `sensor` (omit `sensor` to hit all nodes). `restart` = the node acks (`node_status` `restarting`, below) and **exits** — systemd (`Restart=always`) relaunches it, and the service's ExecStartPre auto-update (`deploy/update-node.sh`) **pulls the latest code first**, so restart == update+restart (~10–20 s to streaming; the background plate persists). `reboot` = ack (`rebooting`) then `sudo -n reboot` — needs the passwordless-sudo rule `deploy/install-node-service.sh` writes to `/etc/sudoers.d/` (refused + logged without it); back in ~1–2 min, also auto-updated. |
| `{"cmd":"calibrate_fine","seconds":30,"ball_radius":0.05}` | **rig calibration, Tier-2 wand pass — handled AT THE RELAY** (not forwarded). Collects per-sensor ball centers off the raw clouds for `seconds`, solves the rig (Kabsch), writes `rig_calib.json` and starts registering all sensors on the wire. Optional gate overrides: `min_points`, `max_points`, `max_fit_rms`, `min_pairs`; stationary mode also takes `min_still_sensors` (commit quorum, default 2), `late_join_window` (s a slower camera may still join the current capture after the commit, default 2.5) and `target_captures`. Progress/results stream back as `calib_status` (below). See `docs/rig_calibration.md`. |
| `{"cmd":"calibrate_rough","seconds":10}` | **rig calibration, Tier-1 rough — relay-handled.** When the nodes stream pose keypoints, the session **auto-upgrades to the skeleton solve** (named joints → full 3D Kabsch, ~2–5 cm, `"tier":"skeleton"`; see docs/skeleton_pose.md; optional `min_conf`, `min_joint_pairs`). Fallback with no pose data: per-sensor IMU leveling + the operator's body-centroid track for yaw/XY (~5–10 cm, `"tier":"rough"`; optional `min_points`, `min_pairs`). Either way: walk a slow "L", visible to every camera. |
| `{"cmd":"calibrate_floor","seconds":3}` | **per-sensor floor leveling — relay-handled** (`"tier":"floor"`). Fits each camera's floor plane in its OWN raw cloud (floor must be in view: background subtraction off; empty scene is fine) and composes a per-sensor correction (floor normal → +Y about the floor centroid, common height) onto the current rig transforms (identity if uncalibrated) — every camera's floor comes out flat and coplanar even when each cloud has its own tilt. One rigid viewer-side correction can't do this; that's why it's per-sensor at the relay. Meant for uncalibrated/rough rigs (a fine wand calib is already mm-coplanar — re-leveling it per sensor only degrades registration). The viewer's **Detect Floor** button sends this automatically on multi-camera rigs. |
| `{"cmd":"record_start","name":<optional>}` / `{"cmd":"record_stop"}` | **scene recording — relay-handled** (see the Scene recording section below). Start/stop teeing the outgoing registered `CPV1` stream to a `.cpr` take on the relay's disk. Non-blocking tee: recording never slows the live stream. Idempotent: `record_start` during a take just re-broadcasts the live status. |
| `{"cmd":"list_recordings"}` | **relay-handled**: reply (to the sender) with the `recordings` index message. Rarely needed — the index is pushed on connect and after every stop/delete. |
| `{"cmd":"delete_recording","id":<take id>}` | **relay-handled**: delete a saved take (data + meta); broadcasts the refreshed index. |
| `{"cmd":"reload_rig_calib"}` | **relay-handled**: re-read `rig_calib.json` now (it is also mtime-watched, so this is rarely needed). |
| `{"cmd":"clear_rig_calib"}` | **relay-handled — reset alignment**: cancel any running `calibrate_*` session, delete `rig_calib.json`, stream raw per-camera frames again, and broadcast an empty `rig_poses` (viewers reset gizmos to the origin). The viewer's alignment **Reset** button. |

**`set_camera`** lets the UI choose the camera mode live; the stream adapts (the
node restarts the sensor as needed, re-reads its intrinsics, and re-sends the
`CCAL` handshake — the relay then rebuilds the cloud with **no `CPV1`/viewer
change**). Fields:

- `depth_mode` — depth FOV mode: `NFOV_UNBINNED` (640×576), `NFOV_2X2BINNED`
  (320×288), `WFOV_2X2BINNED` (512×512), `WFOV_UNBINNED` (1024×1024, 15 fps).
  Restarts the sensor.
- `align` — alignment direction (free, per-frame, no restart):
  `depth_to_color` (**default**) streams **one point per color pixel** (depth
  warped into the color grid) → much more color detail / a denser cloud, at more
  points and some depth holes; `color_to_depth` streams **one point per depth
  pixel** (color warped into the depth grid) — fewer, cleaner points. Both
  alignments are registered to the same (depth) frame relay-side via a node-sent
  grid→depth extrinsic, so switching doesn't tilt/shift the cloud — no viewer
  impact.
- `color_resolution` — `720P`/`1080P`/`1440P`/`1536P`/`2160P`/`3072P` (restart).
  **Where it adds real face-color detail:** only in `depth_to_color` (there the
  point grid **is** the color image, so more resolution = more colored points =
  more detail, at the cost of more points and wire) and the future textured-mesh
  render (color decoupled from geometry). In `color_to_depth` the streamed cloud
  is **depth-grid sized**, so color is capped at the depth resolution — one color
  per depth point no matter the capture res; a higher source only *marginally*
  improves each point's single color (better filtering/registration, and a 4:3
  mode covers the depth FOV better than 16:9 720p so fewer edge points come out
  uncolored). It is **free** on that path (identical point count, RVL size and
  wire bytes; only USB + the SDK warp cost more). `1536P` (2048×1536, 4:3) is the
  Orin profile default — a safe high-quality capture default that pays off the
  moment you switch to `depth_to_color` or the mesh.
- `fps` — `5`/`15`/`30`, auto-clamped (WFOV-unbinned & 3072p cap at 15) (restart).

**Keeping `depth_to_color` fast.** One point per color pixel is ~2.5× the
mask/RVL/color work and wire of `color_to_depth`, so if it dips below 30 fps use
the existing knobs (no protocol change): keep **background subtraction on** (the
biggest win — a subtracted subject stays point-count-bound at 30 fps in either
alignment), run the relay with **`--wire cpv2`** (~52 % smaller wire) and
**`--workers`** on a many-core central box, and give a heavy node more
**`--workers`** (the Orin profile uses 4). These make `depth_to_color` *usable*
but never as cheap as `color_to_depth`; the per-vertex color detail is still
capped by point count, and the holes are inherent — the architectural fix
(decouple color from geometry via a textured mesh: cheap depth-res geometry + a
full-res color image + per-vertex UVs) is the deferred "texture-as-video" lever.

No ack is sent — the feedback is the cloud changing resolution/density. A camera
change also resets the node's background plate (the grid is a different size), so
the viewer should re-capture the background afterwards.

(`arm` / `record` / `stop` will use this same channel later.)

```js
// viewer: capture a background plate, then stream only the subject
ws.send(JSON.stringify({ cmd: "capture_bg", frames: 60 }));
```

> **Note:** there is no depth near/far range-clip command. The node streams the
> **full depth range** and culls via background subtraction + the speckle filter;
> the old `set_depth` command was removed.

Internally the server re-frames this as a `CTL1` message (magic + u32 len + JSON)
on the node's TCP socket; see `protocol/control.py`. Viewers only speak the JSON
over WebSocket. The three `calibrate_*`/`reload_rig_calib` commands never reach
a node — the relay is the endpoint.

## Downstream: server → browser JSON (text messages)

Alongside the binary `CPV1` frames, the server sends low-rate **text** messages
containing a JSON object with a `"type"` field. Viewers must branch on the
message type (string vs binary) and should ignore unknown `"type"`s (that is
the additive-extension mechanism for this channel).

| message | meaning |
|---|---|
| `{"type":"rig_poses","tier":"fine"\|"rough","ref":<id>,"sensors":{"<id>":{"R":[[…]×3],"t":[x,y,z],"rms":<m>,"pairs":<n>}}}` | **per-sensor camera poses** (view→world, the same transforms applied to the points; R row-major). Sent to each client on connect (if a calibration is active) and broadcast on every calib (re)load. Empty `sensors` = calibration cleared — reset gizmos to the origin. |
| `{"type":"calib_status","state":"collecting","tier":…,"seconds_left":<s>,"centers":{"<id>":<n>}}` | live progress of a running `calibrate_*` session (~1 Hz; ~4 Hz for a stationary fine pass). A stationary fine pass adds `mode:"stationary"`, `captures`/`target_captures`/`capturing` and `balls:{"<id>":{c:[x,y,z],rms,n,still,cap}}` — the per-sensor detected ball centre in that sensor's wire-cloud frame; `still` = that camera's detection has settled, `cap` = its sample is **committed into the current capture** (including a late join: the capture stays open `late_join_window` s after the quorum commit so slower-settling cameras append to the same spot/id). The operator holds each spot until every camera that can see the ball shows `cap`. |
| `{"type":"calib_status","state":"done","tier":…,"sensors":{"<id>":{"rms":<m>,"pairs":<n>}},"unsolved":[…]}` | the solve finished and was applied; per-sensor residuals (mm-scale rms = good wand pass). `unsolved` lists sensors that had tracks but too few matched pairs. |
| `{"type":"calib_status","state":"failed","reason":…}` / `{"state":"busy"}` / `{"state":"cancelled"}` | nothing usable was collected / a session is already running / a running session was cancelled by `clear_rig_calib` (sent after the clear, so it supersedes any in-flight `collecting`). |
| `{"type":"skeleton","sensor":<id>,"joints":{"<joint_id>":[x,y,z,conf]}}` | **live 3D pose joints** for one sensor (docs/skeleton_pose.md), sent whenever that node ships pose keypoints (`CPOS`) — up to ~frame rate. COCO-17 joint ids; coordinates in the SAME frame as that sensor's `CPV1` points (view frame, or the shared world frame once a rig calibration is applied), so the viewer can draw them straight onto the cloud. |
| `{"type":"record_status","state":"recording","id":…,"name":…,"seconds":<s>,"frames":<n>,"bytes":<n>,"dropped":<n>}` | **scene recording is running** — broadcast on `record_start` and ~1 Hz while active (and sent to each client on connect if a take is running), so every viewer's Record button/status stays in sync. `dropped` > 0 means the relay's disk can't keep up (frames were skipped rather than stalling the live stream). |
| `{"type":"record_status","state":"saved","recording":{…meta…}}` / `{"state":"idle"}` | a take finished and was written (meta = the recordings-index entry, below) / a `record_stop` arrived with nothing running (resets a stale panel). |
| `{"type":"recordings","items":[{"id","name","created","duration","frames","bytes","sensors":[…],"max_count","dropped","format":"CPR1","version":1}, …]}` | the **saved-takes index**, newest first — sent to each client on connect, after every stop/delete, and on request (`list_recordings`). `max_count` is the densest frame's point count (viewers size playback buffers from it). |
| `{"type":"cmd_error","cmd":…,"error":"unknown command"}` | the relay received a browser command it doesn't recognise (sent only to that client). The usual cause is a **viewer newer than the relay** — the viewer should tell the operator to update/restart the relay instead of hanging on an optimistic status. |
| `{"type":"node_status","sensor":<id>,"event":"bg_captured"}` | a **node status event** (the node's `CSTA` message rebroadcast, 2026-07-10). `bg_captured` = that node's background plate finished averaging (or was reloaded from disk after a node restart — plates persist now) and **subtraction is now live** on its stream — the truthful signal the viewer's background status line uses per camera (the old UI could only guess with a timer, which lied whenever the plate ran slower than assumed). The relay caches the last event per sensor and **replays it to each new viewer on connect** (cleared by `clear_bg`), so a reloaded page shows the true state. Old nodes never send it; viewers keep a timer fallback. NB the node can OPT IN to a low `--setup-fps` throttle until subtraction is active (default 0 = no throttle — a default-on 2 fps setup view read as "the Kinects broke" on first deployment). |
| `{"type":"node_status","sensor":<id>,"event":"restarting"\|"rebooting"}` | **node_admin ack** (2026-07-11): that node received `restart`/`reboot` and is going down now. Broadcast-only — unlike `bg_captured` these are transient and are **never cached/replayed** to new viewers (a cached one would both stomp the replayed bg state and tell a late joiner the node is "restarting" long after it's back). Recovery has no event: the node simply streams again (and re-acks `bg_captured` when its persisted plate reloads). |

## Scene recording (record the live stream, replay it in the scene)

The relay can **record the live scene while it plays** — the `record_start`
command tees every outgoing `CPV1` message (already RVL-decoded, unprojected,
background-subtracted and rig-registered — exactly what viewers render) into a
take file on the relay's disk. The tee is an O(1), non-blocking enqueue on the
node threads with a dedicated writer thread behind it, so **recording never
degrades the live/VR experience**; if the disk falls behind a large buffer,
frames are dropped-and-counted instead of stalling the stream. Because the
recorded format IS the wire format, playback feeds the same source-agnostic
renderer and recorded content is indistinguishable from live (the North Star).
Implementation: `central/recording.py`; storage: `--recordings-dir`
(default `recordings/`).

### Take container: `CPR1` (little-endian)

```
header: magic "CPR1" (4s) | u16 version = 1 | u16 reserved
frame:  f64 t (seconds since recording start) | u32 len | one CPV1 message
```

A JSON sidecar `<id>.json` next to the `.cpr` holds the meta (the
recordings-index entry). Frames are chronological; `t` drives playback pacing.
A truncated tail (crash mid-write) is valid — readers stop at the last intact
frame. Bump the magic (`CPR2`, …) for breaking changes.

### HTTP endpoints (same port as the WebSocket)

The relay answers **plain HTTP** on the ws port (it already speaks HTTP for
the upgrade handshake), CORS-open (`Access-Control-Allow-Origin: *`) so any
origin — the Vite dev viewer today, the recording-playback web app later —
can fetch:

- `GET /recordings` → the saved-takes index as JSON (same items as the
  `recordings` WS message).
- `GET /recordings/<id>` → that take's `CPR1` file
  (`application/octet-stream`).

```js
// viewer: derive the HTTP base from the ws URL, fetch + play a take
const base = wsUrl.replace(/^ws/, 'http')
const take = await (await fetch(`${base}/recordings/${id}`)).arrayBuffer()
```

Sizing rule of thumb: a background-subtracted subject (~25k pts, rgb + grid)
records ~0.5 MB/frame ≈ **~0.9 GB/min at 30 fps**. Record with background
subtraction ON (that's also the intended artistic use — subject-only takes);
full-room recordings get big fast.

## Versioning

Bump the magic (`CPV2`, …) on any breaking layout change. Additive optional
blocks should use a new `flags` bit so older viewers can ignore them.
