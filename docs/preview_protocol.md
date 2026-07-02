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
- **permessage-deflate (RFC 7692)** is negotiated when the client offers it
  (browsers do by default; `no_context_takeover` both ways). Transparent to the
  message layout below — the browser's WebSocket API hands the viewer the
  decompressed bytes. Measured ~1.6× on the wire (the delta-encoded positions
  are what make the payload compressible at all).
- Best-effort over a reliable socket (WebSocket = TCP). If a client is
  slow, the server drops frames to it rather than buffering unbounded (each
  viewer has its own latest-frame outbox). Lower latency transports
  (WebRTC/WebTransport) are a later swap; the *message body*
  below is transport-independent.

## Message: `CPV2` (PreviewFrame), little-endian

| field | type | meaning |
|---|---|---|
| magic | `4s` | `CPV2` |
| flags | `u32` | bit0 = positions present (always 1); bit1 = `rgb` present; bit2 = `gravity` present |
| sensor_id | `u32` | source sensor (0..N-1) |
| frame_id | `u32` | capture frame index (low 32 bits) |
| count | `u32` | number of points |

Then the payload blocks, in order:

1. **bbox** — `6 × float32`: the frame's bounding-box **origin** (x,y,z, metres)
   then the per-axis **scale** (metres per quantization step,
   `(max − min) / 65535`; 0 on a degenerate axis).
2. **positions** — `count × 3 × uint16`, **bbox-quantized and per-axis
   delta-encoded** in point order: row 0 is the absolute quantized value, every
   later row is the difference from the previous point's value, **wrapping mod
   2^16**. Decode by accumulating per axis (`q += delta`, keep 16 bits), then
   `p = origin + q * scale`. Points arrive in row-major grid order, so deltas
   are small and compress well under permessage-deflate. World frame unchanged:
   `x` right, `y` up, `z` toward viewer (camera looks down −z). Worst-case
   quantization error is `scale/2` per axis (≈0.02 mm for a ~3 m box) — far
   below sensor noise (the source depth is integer millimetres).
3. **rgb** *(only if flag bit1 set)* — `count × 3 × uint8`, 0–255 per channel,
   one triple per point (same order as positions). Sent when the node provides
   depth-aligned color (`kinect_node` via `transformed_color`; `sim_node`
   always). The relay sets bit1 whenever it has color for the frame; a viewer
   must still handle bit1 = 0 (geometry only) gracefully.
4. **gravity** *(only if flag bit2 set)* — `3 × float32`, a **gravity (down)
   unit vector** in the same view/world frame as positions, derived from the
   sensor's IMU accelerometer. It gives the cloud an initial orientation (which
   way is down / where the floor lies) before any extrinsic calibration. Static-
   ish (the rig doesn't move) but attached to every frame so a late-joining
   viewer always has it. **Read it with a `DataView` (`getFloat32`), not a
   `Float32Array` view:** this block starts at a non-4-byte-aligned offset and
   a typed-array view would throw.

Only valid (non-zero-depth) points are sent, after a stride-based downsample —
so `count` varies per frame. The viewer must read `count` from the header, not
assume a fixed size. Offsets: bbox at `20`, positions at `44`, `rgb` (when
present) at `44 + count*6`, `gravity` right after (`44 + count*6`, plus
`count*3` when rgb is present).

Cost: 9 B/pt with color (CPV1 was 15) → with deflate ≈ **33 Mbps at 30 fps for
a 25k-pt subject** (CPV1 was 89) — the difference between "needs fiber" and
"streams over ordinary broadband".

### Legacy: `CPV1`

Same header; positions were `count × 3 × float32` metres at offset 20 (no bbox
block), rgb at `20 + count*12`, gravity after. The viewer retains a CPV1 parse
path for old relays; the relay only emits CPV2.

## Viewer side (sketch, lives in `crypt`)

```js
ws.binaryType = "arraybuffer";
ws.onmessage = (e) => {
  const dv = new DataView(e.data);
  // magic @0..3 === "CPV2"; flags @4; sensor @8; frame @12; count @16
  const count = dv.getUint32(16, true);
  const o = [dv.getFloat32(20, true), dv.getFloat32(24, true), dv.getFloat32(28, true)];
  const s = [dv.getFloat32(32, true), dv.getFloat32(36, true), dv.getFloat32(40, true)];
  const q = new Uint16Array(e.data, 44, count * 3);
  const positions = new Float32Array(count * 3);
  let ax = 0, ay = 0, az = 0;                  // per-axis accumulators
  for (let i = 0; i < count; i++) {
    ax = (ax + q[i * 3]) & 0xffff;
    ay = (ay + q[i * 3 + 1]) & 0xffff;
    az = (az + q[i * 3 + 2]) & 0xffff;
    positions[i * 3] = o[0] + ax * s[0];
    positions[i * 3 + 1] = o[1] + ay * s[1];
    positions[i * 3 + 2] = o[2] + az * s[2];
  }
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
| `{"cmd":"set_imu","enabled":<bool>}` | **stream live IMU orientation.** When enabled, the node re-reads the accelerometer every ~10 frames and re-sends a fresh gravity (down) vector, so the cloud reorients live as the camera is physically turned. Off by default (one gravity vector is still sent at connect). The gravity rides in the `CPV2` gravity block (bit2). |

**`set_camera`** lets the UI choose the camera mode live; the stream adapts (the
node restarts the sensor as needed, re-reads its intrinsics, and re-sends the
`CCAL` handshake — the relay then rebuilds the cloud with **no `CPV2`/viewer
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
over WebSocket.

## Versioning

Bump the magic (`CPV2`, …) on any breaking layout change. Additive optional
blocks should use a new `flags` bit so older viewers can ignore them.
