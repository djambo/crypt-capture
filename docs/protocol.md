# Wire protocol & take format

## Frame message (TCP, little-endian) — see `protocol/frame.py`

36-byte header + payload:

| field | type | meaning |
|---|---|---|
| magic | 4s | `CVF1` |
| sensor_id | u8 | 0..N-1 |
| flags | u8 | bit0 = depth is RVL-compressed; bit1 = color is depth-aligned RGB |
| stride | u16 | node-side preview downsample (1 = full res); pixel (u,v) → original (u·stride, v·stride) |
| frame_id | u64 | **hardware-synced** frame index (groups sensors) |
| timestamp_ns | u64 | node capture time |
| width,height | u16,u16 | (strided) depth resolution |
| depth_len,color_len | u32,u32 | payload sizes |

When `bit1` (aligned color) is set, the color payload is raw `uint8` RGB for the
foreground (non-zero depth) pixels only, row-major, one triple per pixel.

Payload = `depth_bytes ++ color_bytes`. Depth is RVL (`protocol/rvl.py`); color
is an opaque encoded blob (NVENC H.26x on real nodes; a stub in the simulator).

One TCP connection per node → central. The recorder groups frames by `frame_id`;
a frame is "complete" once all N sensors delivered it.

## Take on disk — see `central/recorder.py`

```
<take>/
  manifest.json                         # sensors, resolution, frame index, calibration
  frames/<frame_id:06d>/sensorN.depth.rvl
  frames/<frame_id:06d>/sensorN.color.bin
```

Depth is stored RVL as-received (no decode on the record hot path). Offline
processing (calibrate → fuse → mesh) consumes the take.
