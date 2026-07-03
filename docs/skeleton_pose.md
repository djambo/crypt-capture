# Skeleton / pose pipeline — design + wiring

Human 2D pose estimation on the nodes' color images, lifted to metric 3D via
the depth maps. It pays twice:

1. **Skeleton-based rig alignment** — a better rough tier. The body CENTROID
   (the old rough landmark) is biased toward each camera by ~half the body
   depth and supports only a yaw-constrained solve. A named JOINT (shoulder,
   hip, ankle…) is semantically the *same physical 3D point* from every
   viewpoint: ~17 joints × a 10 s walk = thousands of true 3D↔3D
   correspondences → full 3D Kabsch per sensor, no IMU dependency.
   Expected **~2–5 cm** (2D keypoint jitter + depth is read on the body
   surface, not at the joint center). The wand pass remains the ~mm tier.
2. **Creative inputs** — live 3D joints in the shared world frame: hands as
   particle attractors, gesture triggers, and later SMPL-X fitting seeded by
   the 2D skeletons ("approach C").

## Model choice (decision)

- **RTMPose (Apache-2.0), TensorRT-compiled — the plan of record.** Fast
  (RTMPose-m ≥ 30 fps on an Orin at 640), accurate, license-clean.
- **YOLOv8/11-pose** — great and simple, but Ultralytics is **AGPL-3.0**
  (same flag as RVM in CLAUDE.md). Fallback only if RTMPose disappoints.
- **DeepStream: rejected.** It's NVIDIA's multi-stream *video pipeline*
  framework (GStreamer/RTSP graphs). Our node isn't a video pipeline — it's
  Python holding pyk4a numpy frames. We want the inference engine underneath
  (TensorRT), not the pipeline framework around it.
- MoveNet / trt_pose (Apache/MIT): lighter fallbacks if the model must shrink.

## Where inference runs (decision): on the node, decoupled — central optional

**Primary: on each node's GPU.** The capture pipeline is CPU-bound (RVL +
color); the Jetson GPU sits idle — pose costs the streaming path nothing if
it never touches it:

- Inference runs in its **own worker** (thread driving the GPU, or a process
  like `_process_frame`'s pool), consuming the **latest color frame only**
  (freshness-beats-completeness, same rule as the frame pipeline). If the
  model runs at 10–15 fps while capture runs at 30, every cloud frame still
  ships on time and keypoints ride along at their own rate.
- **The cloud stream never waits on pose.** Keypoints are a separate tiny
  CPOS message (~400 B); a slow or crashed pose worker degrades only the
  skeleton stream.
- Scales with the rig: 4 cameras = 4 GPUs, no central bottleneck.

**Central-side inference is a supported fallback, not a worse afterthought**
— it's how weak nodes (the 1st-gen Nano: JetPack 4, ~0.5 TFLOPS) get
skeletons without hardware upgrades. The relay already reconstructs each
sensor's depth-aligned color grid (`aligned_color_grid`) per frame, so it can
run the same model there and inject keypoints into the identical internal
path — **no protocol change**. Trade-offs: the wire's color is
foreground-masked (subject-only images are slightly out-of-distribution for
pose models, though usually fine), the central machine needs GPU headroom ×
N sensors, and it only sees color when nodes stream it. Decision: build
node-side first; add `--pose-central` to the relay if/when the Nano needs it.

## Wire format: `CPOS` (node → central, implemented)

Rides the node's existing TCP stream next to CCAL/CIMU/CEXT
(`protocol/frame.py`): magic `CPOS`, sensor_id (u32), timestamp_ns (u64),
count (u8), then per keypoint `(joint_id u8, u f32, v f32, z_m f32, conf
f32)`. `joint_id` = COCO-17. `u, v` are FULL-RESOLUTION pixel coords on the
current grid — the color image is pixel-aligned with depth in both alignment
modes, so (u, v) indexes the depth grid directly. `z_m` is the depth the node
read at that pixel (metres; small-neighbourhood median recommended on real
depth; 0 = no depth, consumer skips). The node ships 2D+z; **central owns the
3D lift** (per-sensor ray table incl. lens distortion + grid→depth extrinsic
+ optical→view flip), so node code stays trivial and 3.6-safe.

## Central + viewer (implemented)

- `preview_server._on_pose`: unproject each keypoint → view-frame 3D joint;
  feed any active skeleton-capable calibration session with the RAW joints;
  apply the rig transform; broadcast
  `{"type":"skeleton","sensor":id,"joints":{"<jid>":[x,y,z,conf]}}` (TEXT) to
  viewers — same frame as that sensor's cloud.
- **Rough Align auto-upgrades**: `calibrate_rough` collects BOTH the centroid
  track and a `JointTracker`. At solve time, if joints matched (≥
  `min_joint_pairs`, default 60) → `solve_skeleton` (per-joint
  `pair_tracks`, stacked, full `solve_rigid`), tier **"skeleton"**; else the
  centroid+IMU `solve_rough` fallback, tier "rough". One button, best
  available solve. Floor leveling composes on top of either.
- Viewer: `skeleton` messages draw per-sensor tinted joint markers
  (`SkeletonMarkers`, in the sensor's group so they sit on its cloud;
  auto-hide when stale; "skeletons" layer toggle).

## Headless testing (implemented)

`sim_node --skeleton` emits a synthetic wall-clock-driven person (9 COCO
joints, wandering pelvis + waving arm) projected through the sensor's
`--pose` — exactly what a real node ships. Two posed sims + relay: Rough
Align must auto-upgrade to the skeleton tier and recover the ground-truth
pose. `tests/test_pose.py` covers the CPOS round-trip, JointTracker gates,
`solve_skeleton` (recovers 55° / 1.3 m to <1°/2 cm under 1.5 cm joint noise)
and the sim projection round-trip.

## Node inference worker (implemented — `node/pose.py`)

v1 model decision revised for zero-friction deployment: **MoveNet single-pose
(Apache-2.0) via onnxruntime** — single-person (matches the capture volume),
one ONNX file, trivial decode, no cv2/mmcv, and plain-pip installable on
JetPack. RTMPose/TensorRT remains the accuracy/speed upgrade path if MoveNet
proves limiting (the estimator interface is one class to swap).

- `MoveNetEstimator`: tolerant of the common ONNX export variants (NHWC/NCHW,
  int32/uint8/float input, any output list containing one 17×3 tensor);
  letterbox + inverse decode in pure NumPy.
- `PoseWorker`: own thread, latest-frame-only, capped `intra_op` CPU threads;
  keypoints + 5×5-median depth lookup → `encode_pose` → the node's ordered
  sender queue (socket writes stay serialised). Prints `pose N fps` every 150
  inferences.
- `kinect_node --pose-model <file.onnx> [--pose-threads 2]
  [--pose-min-conf 0.2]` — off by default; onnxruntime is only imported when
  enabled, so nodes without it are unaffected.

### Enabling it on a Jetson (Orin, JetPack 6)

```bash
# on the Orin:
cd ~/crypt-capture && git pull
pip3 install onnxruntime                    # CPU aarch64 wheel; GPU optional later
mkdir -p models
# MoveNet single-pose ONNX (Apache-2.0), pick ONE:
#   Thunder (256px, more accurate, ~2-3x slower) — recommended first:
curl -L -o models/movenet.onnx \
  https://huggingface.co/Xenova/movenet-singlepose-thunder/resolve/main/onnx/model.onnx
#   Lightning (192px, fastest):
# curl -L -o models/movenet.onnx \
#   https://huggingface.co/Xenova/movenet-singlepose-lightning/resolve/main/onnx/model.onnx

# test run in the foreground (stop the service first):
sudo systemctl stop kinect-node
python3 -m node.kinect_node --host auto --sensor 0 --frames 0 \
    --preview-stride 2 --pose-model models/movenet.onnx --profile
# expect: "sensor 0: pose model models/movenet.onnx (input ...)" at startup,
# "sensor 0 pose: N fps ..." lines while running, and your skeleton in the
# browser the moment you step in. Watch the frame fps line: it must hold the
# same rate as without --pose-model (that's the decoupling contract).

# make it permanent: add the flag to EXTRA_ARGS in /etc/default/kinect-node
#   EXTRA_ARGS=--preview-stride 2 --pose-model models/movenet.onnx
sudo systemctl start kinect-node
```

`models/` is gitignored, so the service's auto-update (fetch + hard reset)
never touches the downloaded file. If joints appear but confidences are ~0 or
the skeleton looks wrong, try the other MoveNet variant and report — the
estimator logs the detected input signature at startup.

## Remaining

1. Bench on the Orin (pose fps + confirm cloud fps unchanged via `--profile`);
   optional `onnxruntime-gpu` (NVIDIA's Jetson wheel) or the RTMPose/TensorRT
   upgrade if CPU inference is too slow or contends with the RVL workers.
2. Decide the Nano: skip pose there, or central-side fallback (above).
3. Creative hooks: hands (joints 9/10) → particle attractors in the viewer;
   gesture triggers later.

Accuracy honesty, restated: skeleton align ≈ 2–5 cm — it *replaces rough*,
not the wand. Fine (ball) stays the calibration for recording/VR/fusion.
