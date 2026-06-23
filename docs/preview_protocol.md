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
| flags | `u32` | bit0 = positions present (always 1); bit1 = `rgb` present |
| sensor_id | `u32` | source sensor (0..N-1) |
| frame_id | `u32` | capture frame index (low 32 bits) |
| count | `u32` | number of points |

Then the payload blocks, in order:

1. **positions** — `count × 3 × float32`, metres, in view/world space
   (`x` right, `y` up, `z` toward viewer i.e. camera looks down −z). Ready to
   drop into a three.js `Float32Array` position attribute.
2. **rgb** *(only if flag bit1 set)* — `count × 3 × uint8`, 0–255 per channel.
   Reserved for when the node ships depth-aligned color; **v0 sends geometry
   only** (bit1 = 0).

Only valid (non-zero-depth) points are sent, after a stride-based downsample —
so `count` varies per frame. The viewer must read `count` from the header, not
assume a fixed size.

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

## Versioning

Bump the magic (`CPV2`, …) on any breaking layout change. Additive optional
blocks should use a new `flags` bit so older viewers can ignore them.
