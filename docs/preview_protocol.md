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
| `{"cmd":"set_depth","min":<mm>,"max":<mm>}` | set the working depth mask; pixels outside `[min,max]` mm are dropped (background removal). Either field optional. |
| `{"cmd":"capture_bg","frames":<n>}` / `{"cmd":"clear_bg"}` / `{"cmd":"set_bg_margin","mm":<n>}` | background-plate subtraction (snapshot the empty scene, then stream only the subject). |
| `{"cmd":"set_denoise","min_neighbors":<n>}` | speckle filter strength (0 = off). |
| `{"cmd":"set_camera", "depth_mode":<m>, "color_resolution":<r>, "fps":<f>, "align":<a>}` | **pick which Kinect data to send** (all fields optional; unknown/unchanged ignored). See below. |

**`set_camera`** lets the UI choose the camera mode live; the stream adapts (the
node restarts the sensor as needed, re-reads its intrinsics, and re-sends the
`CCAL` handshake — the relay then rebuilds the cloud with **no `CPV1`/viewer
change**). Fields:

- `depth_mode` — depth FOV mode: `NFOV_UNBINNED` (640×576), `NFOV_2X2BINNED`
  (320×288), `WFOV_2X2BINNED` (512×512), `WFOV_UNBINNED` (1024×1024, 15 fps).
  Restarts the sensor.
- `align` — alignment direction (free, per-frame, no restart):
  `color_to_depth` (default) streams **one point per depth pixel** (color warped
  into the depth grid); `depth_to_color` streams **one point per color pixel**
  (depth warped into the color grid) → much more color detail / a denser cloud,
  at more points and some depth holes.
- `color_resolution` — `720P`/`1080P`/`1440P`/`1536P`/`2160P`/`3072P` (restart;
  mostly matters in `depth_to_color`, where the point grid IS the color image).
- `fps` — `5`/`15`/`30`, auto-clamped (WFOV-unbinned & 3072p cap at 15) (restart).

No ack is sent — the feedback is the cloud changing resolution/density. A camera
change also resets the node's background plate (the grid is a different size), so
the viewer should re-capture the background afterwards.

(`arm` / `record` / `stop` will use this same channel later.)

```js
// viewer: set the depth mask to 0.4–4.0 m
ws.send(JSON.stringify({ cmd: "set_depth", min: 400, max: 4000 }));
```

Internally the server re-frames this as a `CTL1` message (magic + u32 len + JSON)
on the node's TCP socket; see `protocol/control.py`. Viewers only speak the JSON
over WebSocket.

## Versioning

Bump the magic (`CPV2`, …) on any breaking layout change. Additive optional
blocks should use a new `flags` bit so older viewers can ignore them.
