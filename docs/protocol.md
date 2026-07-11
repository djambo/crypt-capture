# Wire protocol & take format

## Frame message (TCP, little-endian) — see `protocol/frame.py`

36-byte header + payload:

| field | type | meaning |
|---|---|---|
| magic | 4s | `CVF1` |
| sensor_id | u8 | 0..N-1 |
| flags | u8 | bit0 = depth is RVL-compressed; bit1 = color is depth-aligned RGB; bit2 = color payload is bbox + JPEG (see below) |
| stride | u16 | node-side preview downsample (1 = full res); pixel (u,v) → original (u·stride, v·stride) |
| frame_id | u64 | **hardware-synced** frame index (groups sensors) |
| timestamp_ns | u64 | node capture time |
| width,height | u16,u16 | (strided) depth resolution |
| depth_len,color_len | u32,u32 | payload sizes |

When `bit1` (aligned color) is set, the color payload is raw `uint8` RGB for the
foreground (non-zero depth) pixels only, row-major, one triple per pixel.

When `bit2` (**JPEG color**, 2026-07-10) is also set, the color payload is
instead `u16 x0, y0, bw, bh` (bounding box of the valid pixels on the strided
grid) followed by a JPEG of the aligned color image cropped to that bbox. The
relay decodes it back onto the full grid (`jpeg_color_grid`) and samples it
through the depth valid mask, so everything downstream of the decode is
identical to the raw path. Rationale: the raw triples are ~75 % of a
background-subtracted subject frame's bytes; the JPEG is ~5-8× smaller — the
difference between a WiFi link carrying 3 cameras at 30 fps or at ~8. Node
side: `kinect_node --color-jpeg-quality` (default 80; 0 = raw; falls back to
raw automatically without cv2/Pillow). Relay side needs cv2 or Pillow to
decode (loud one-time console instruction otherwise, points render white).
IR-color mode keeps the raw path.

Payload = `depth_bytes ++ color_bytes`. Depth is RVL (`protocol/rvl.py`); color
is an opaque encoded blob (NVENC H.26x on real nodes; a stub in the simulator).

One TCP connection per node → central. The recorder groups frames by `frame_id`;
a frame is "complete" once all N sensors delivered it.

## Intrinsics handshake (TCP, node → central) — `protocol/frame.py`

On connect, each node sends its **own** depth-camera intrinsics once, before any
frames, so central needs no per-device calib files and scales to N cameras:

| field | type | meaning |
|---|---|---|
| magic | 4s | `CCAL` |
| sensor_id | u32 | which sensor these intrinsics are for |
| width,height | u16,u16 | full-res depth dims the intrinsics apply to |
| fx,fy,cx,cy | f32×4 | pinhole intrinsics (full resolution) |
| k1,k2,p1,p2,k3,k4,k5,k6 | f32×8 | Brown-Conrady distortion (OpenCV order); central undistorts via a ray table |

Both message types share the node→central stream; readers dispatch on the
leading 4-byte magic (`read_message` returns `("frame", …)` or `("calib", …)`).
Central keys intrinsics by `sensor_id`; the relay's `--calib` file is now just an
optional override / fallback for nodes that don't send them.

## Take on disk — see `central/recorder.py`

```
<take>/
  manifest.json                         # sensors, resolution, frame index, calibration
  frames/<frame_id:06d>/sensorN.depth.rvl
  frames/<frame_id:06d>/sensorN.color.bin
```

Depth is stored RVL as-received (no decode on the record hot path). Offline
processing (calibrate → fuse → mesh) consumes the take.
