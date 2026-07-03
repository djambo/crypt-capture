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
| flags | `u32` | bit0 = positions present (always 1); bit1 = `rgb` present; bit2 = `gravity` present |
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

Only valid (non-zero-depth) points are sent, after a stride-based downsample —
so `count` varies per frame. The viewer must read `count` from the header, not
assume a fixed size. The `rgb` block, when present, starts at byte `20 +
count*12`; the `gravity` block starts right after it (`20 + count*12`, plus
`count*3` when rgb is present).

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
| `{"cmd":"set_camera", "depth_mode":<m>, "color_resolution":<r>, "fps":<f>, "align":<a>}` | **pick which Kinect data to send** (all fields optional; unknown/unchanged ignored). See below. |
| `{"cmd":"set_imu","enabled":<bool>}` | **stream live IMU orientation.** When enabled, the node re-reads the accelerometer every ~10 frames and re-sends a fresh gravity (down) vector, so the cloud reorients live as the camera is physically turned. Off by default (one gravity vector is still sent at connect). The gravity rides in the `CPV1` gravity block (bit2). |
| `{"cmd":"calibrate_fine","seconds":30,"ball_radius":0.05}` | **rig calibration, Tier-2 wand pass — handled AT THE RELAY** (not forwarded). Collects per-sensor ball centers off the raw clouds for `seconds`, solves the rig (Kabsch), writes `rig_calib.json` and starts registering all sensors on the wire. Optional gate overrides: `min_points`, `max_points`, `max_fit_rms`, `min_pairs`. Progress/results stream back as `calib_status` (below). See `docs/rig_calibration.md`. |
| `{"cmd":"calibrate_rough","seconds":10}` | **rig calibration, Tier-1 rough — relay-handled.** Per-sensor IMU leveling + the operator's body-centroid track for yaw/XY (~5–10 cm, zero props; walk a small "L"). Optional `min_points`, `min_pairs`. Same file/flow as fine, `"tier":"rough"`. |
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
- `color_resolution` — `720P`/`1080P`/`1440P`/`1536P`/`2160P`/`3072P` (restart;
  mostly matters in `depth_to_color`, where the point grid IS the color image).
- `fps` — `5`/`15`/`30`, auto-clamped (WFOV-unbinned & 3072p cap at 15) (restart).

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
| `{"type":"calib_status","state":"collecting","tier":…,"seconds_left":<s>,"centers":{"<id>":<n>}}` | live progress of a running `calibrate_*` session (~1 Hz). |
| `{"type":"calib_status","state":"done","tier":…,"sensors":{"<id>":{"rms":<m>,"pairs":<n>}},"unsolved":[…]}` | the solve finished and was applied; per-sensor residuals (mm-scale rms = good wand pass). `unsolved` lists sensors that had tracks but too few matched pairs. |
| `{"type":"calib_status","state":"failed","reason":…}` / `{"state":"busy"}` / `{"state":"cancelled"}` | nothing usable was collected / a session is already running / a running session was cancelled by `clear_rig_calib` (sent after the clear, so it supersedes any in-flight `collecting`). |

## Versioning

Bump the magic (`CPV2`, …) on any breaking layout change. Additive optional
blocks should use a new `flags` bit so older viewers can ignore them.
