# Wire protocol & take format

## Frame message (TCP, little-endian) — see `protocol/frame.py`

36-byte header + payload:

| field | type | meaning |
|---|---|---|
| magic | 4s | `CVF1` |
| sensor_id | u8 | 0..N-1 |
| flags | u8 | bit0 = depth is RVL-compressed |
| reserved | u16 | |
| frame_id | u64 | **hardware-synced** frame index (groups sensors) |
| timestamp_ns | u64 | node capture time |
| width,height | u16,u16 | depth resolution |
| depth_len,color_len | u32,u32 | payload sizes |

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
