# Graph Report - .  (2026-07-07)

## Corpus Check
- 68 files · ~91,377 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 826 nodes · 1386 edges · 58 communities (51 shown, 7 thin omitted)
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 48 edges (avg confidence: 0.61)
- Token cost: 220,389 input · 0 output

## Community Hubs (Navigation)
- Preview Relay Server
- Wire Formats & Design Concepts
- RVL Codec & Meshing
- Hardware & Data-Quality Strategy
- Temporal Depth Denoise
- Rough Rig Solve
- WebSocket & Client Tools
- Scene Recording
- Kinect Node Pipeline
- Jetson Setup & GPU-Unproject
- Rig Calibration Solver
- Ball Fit & Calibration Tests
- Spatial Depth Denoise
- Simulated Node
- Per-Viewer Sender
- Frame Protocol & Relay Ingest
- Recording HTTP Tests
- Background Subtraction
- LAN Auto-Discovery
- Relay Unprojection Helpers
- MoveNet Pose Estimator
- Skeleton Solve Tests
- One-Euro Joint Smoothing
- Stationary Ball Sampler
- Take Recorder & Demo
- Pose Worker Thread
- Frame Wire I/O
- CPV3 Reconstruction Tests
- XR Pose Passthrough
- CPV2 Message Encoding
- Pose Inference Process
- Camera Mode Tables
- IMU Path Tests
- Floor Leveling
- Control Plane
- Pose Model Tests
- Joint Tracker
- IMU Gravity Reading
- Extrinsic Registration Tests
- Texture UV Tests
- Floor Sampler
- CPV2/UV Serialization
- Camera Mode Tests
- Pose Gate Hysteresis Tests
- Keypoint Depth Sampling
- Grid Block Tests
- CPV3/Texture Serialization
- Unproject & Color UV
- Node Launch Script
- Node Auto-Update Script
- Calibration Dump
- Core Design Principles
- Node Service Install
- Compute Bottleneck Rationale

## God Nodes (most connected - your core abstractions)
1. `PreviewServer` - 66 edges
2. `run()` - 19 edges
3. `PoseWorker` - 19 edges
4. `ClientSender` - 18 edges
5. `TemporalDepthFilter` - 18 edges
6. `SpatialDepthFilter` - 16 edges
7. `run()` - 16 edges
8. `MoveNetEstimator` - 15 edges
9. `Frame` - 15 edges
10. `_FakeEstimator` - 15 edges

## Surprising Connections (you probably didn't know these)
- `Per-view AI matting (RVM / BackgroundMattingV2)` --semantically_similar_to--> `Flying-pixel / edge-artifact removal`  [INFERRED] [semantically similar]
  README.md → docs/kinect_data_improvements.md
- `_FakeEstimator` --uses--> `JointTracker`  [INFERRED]
  tests/test_pose.py → central/calibration.py
- `_ScriptedEstimator` --uses--> `JointTracker`  [INFERRED]
  tests/test_pose.py → central/calibration.py
- `ClientSender` --uses--> `MoveNetEstimator`  [INFERRED]
  central/preview_server.py → node/pose.py
- `ClientSender` --uses--> `PoseWorker`  [INFERRED]
  central/preview_server.py → node/pose.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Jetson Orin Kinect bring-up gotchas** — docs_jetson_orin_migration_libsoundio1, docs_jetson_orin_migration_depth_engine_gl, docs_jetson_orin_node_setup_udev, docs_jetson_orin_migration_cold_boot_enum [EXTRACTED 0.90]
- **A-lite browser-GPU unprojection stack** — docs_gpu_unproject_a_lite, docs_gpu_unproject_cpv3, docs_gpu_unproject_ray_table_texture, docs_gpu_unproject_numba_rvl, docs_gpu_unproject_cpv3_shader [EXTRACTED 0.90]
- **Multi-sensor seam-quality techniques** — docs_kinect_data_improvements_cross_sensor_outlier, docs_kinect_data_improvements_photometric_harmonization, docs_kinect_data_improvements_view_angle_blending, docs_kinect_data_improvements_occupancy_carving, readme_tsdf_fusion [EXTRACTED 0.85]
- **Preview cloud wire format family** — docs_preview_protocol_cpv1, docs_preview_protocol_cpv2, docs_preview_protocol_cpv3 [EXTRACTED 0.90]
- **Textured mesh end-to-end flow** — docs_textured_mesh_cclr, docs_textured_mesh_ctex, docs_textured_mesh_uv_projection, docs_textured_mesh_meshcloud [EXTRACTED 0.85]
- **Rig calibration tiers** — docs_rig_calibration_tier1_rough, docs_rig_calibration_tier2_fine, docs_rig_calibration_floor_leveling, docs_skeleton_pose_skeleton_align [EXTRACTED 0.80]

## Communities (58 total, 7 thin omitted)

### Community 0 - "Preview Relay Server"
Cohesion: 0.06
Nodes (21): PreviewServer, Reset alignment: cancel any running calibration session, delete         rig_cal, Broadcast collection progress, then solve when time is up (or, for a         st, Called from the node frame path with the RAW (pre-rig-transform)         view-f, Per-sensor (fx,fy,cx,cy,dist): the node's own (sent on connect) win;         el, Distortion-aware ray table for this sensor's full-res grid (cached)., Heavy STATELESS stage: unproject -> per-sensor rig transform ->         CPV1 me, Broadcast one finished CPV1 frame to viewers, tee it to any active         reco (+13 more)

### Community 1 - "Wire Formats & Design Concepts"
Cohesion: 0.05
Nodes (45): Browser to server control plane, CPR1 take container, CPV1 preview frame format, CPV2 compact wire format, CPV3 browser-GPU unproject format, HTTP /recordings endpoints, rig_poses / calib_status messages, Scene recording (CPR1 take) (+37 more)

### Community 2 - "RVL Codec & Meshing"
Cohesion: 0.07
Nodes (33): frame_to_mesh(), load_calib(), main(), mesh_frame(), Convert a recorded take into per-frame triangle meshes (PLY) you can view.  Th, Unproject a depth grid to a triangle mesh with a depth-discontinuity cut., write_ply(), compress() (+25 more)

### Community 3 - "Hardware & Data-Quality Strategy"
Cohesion: 0.08
Nodes (30): central/recorder.py (synced take writer), crypt-capture pipeline, North Star — indistinguishable real-time vs prerecorded WebXR, One shared world coordinate frame, Live stream == recorded clip representation, Azure Kinect Body Tracking not on ARM/Jetson, Jetson node viability (fiddly for Azure Kinect), Orbbec Femto Bolt (Kinect successor) (+22 more)

### Community 4 - "Temporal Depth Denoise"
Cohesion: 0.09
Nodes (23): EXPERIMENTAL — per-pixel temporal One-Euro filter over the raw depth grid.  Ki, depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length         w*h, Per-sensor, per-pixel One-Euro low-pass over raw depth (millimetres).      min, Drop filter memory (a sensor's, or all). Call when a sensor's         camera mo, _SensorState, TemporalDepthFilter, EXPERIMENTAL temporal depth denoise tests (central/temporal_denoise.py).  Head, A pixel that goes invalid for a long gap (subject moved away) and then     beco (+15 more)

### Community 5 - "Rough Rig Solve"
Cohesion: 0.12
Nodes (23): CentroidTracker, level_rotation(), load_rig_calib(), Accumulates per-sensor (time, foreground-centroid) tracks for the Tier-1     ro, Rotation taking a measured view-frame gravity (down) unit vector onto     world, Best yaw (rotation about +Y) + translation mapping A onto B (N,3 each,     alre, Tier-1 rough rig solve (zero props, ~5-10 cm expected).      tracks: {sensor_i, Load rig_calib.json -> ({sensor_id: (R (3,3) f32, t (3,) f32)}, meta).     meta (+15 more)

### Community 6 - "WebSocket & Client Tools"
Cohesion: 0.10
Nodes (24): accept_key(), client_handshake(), encode_frame(), Minimal WebSocket (RFC 6455) helpers — stdlib only, no dependencies.  Just eno, Encode one WS frame (FIN=1). Mask only for client→server frames., Read one WS frame. Returns (opcode, payload) or None on EOF/close., Compute the Sec-WebSocket-Accept value for a client's key., Read an HTTP request/response head (up to the blank line). (+16 more)

### Community 7 - "Scene Recording"
Cohesion: 0.08
Nodes (18): delete_recording(), list_recordings(), Scene recording: capture the LIVE preview stream to disk, replayable in the vie, Tee one outgoing CPV1 message into the active take. Non-blocking:         store, Live stats for the record_status broadcast (None when idle)., Finish the active take: drain the queue, write the sidecar, return         the, Writer thread: drain the queue to disk until stop() drains us dry.         Also, Yield (t, payload) for every frame of a `.cpr` take. Raises ValueError     on a (+10 more)

### Community 8 - "Kinect Node Pipeline"
Cohesion: 0.11
Nodes (25): denoise_mask(), foreground_mask(), Foreground mask against a *snapshotted* plate (see foreground()). Module-     l, Remove isolated speckles from a boolean foreground mask: drop any kept     pixe, _build_config(), _depth_to_color_extrinsic(), _encode_jpeg(), _grid_to_depth_extrinsic() (+17 more)

### Community 9 - "Jetson Setup & GPU-Unproject"
Cohesion: 0.10
Nodes (25): A-lite (relay decodes, browser unprojects), Browser-GPU unprojection (approach A), CPV3 wire format (depth + bitmap + calib), CPV3 vertex-shader unproject (GpuPointCloud), Full-A (browser RVL decode + shader denoise), Numba RVL decode (approach C, ~10x), Ray-table texture (baked undistortion), PCVR vs standalone: per-client LOD, one architecture (+17 more)

### Community 10 - "Rig Calibration Solver"
Cohesion: 0.13
Nodes (18): BallTracker, pair_tracks(), Rig extrinsic calibration from a tracked marker ball (the "wand" pass).  Why t, Outlier-robust rigid solve (RANSAC around solve_rigid).      The wand pass WIL, Pair two (time, point) tracks by nearest timestamp.      track_*: sequences of, Solve every sensor's rigid transform into a reference sensor's frame.      tra, Accumulates per-sensor (time, ball-center) tracks for the Tier-2 wand     pass., # NOTE: on a FINE (wand) calibrated rig the floors are already coplanar to (+10 more)

### Community 11 - "Ball Fit & Calibration Tests"
Cohesion: 0.15
Nodes (22): fit_sphere(), Rigid transform (R, t) minimising |R·A + t - B|^2 (Kabsch/Umeyama).      A, B:, Center of a sphere of KNOWN radius fitted to surface points (N,3).      Gauss-, Find the marker ball as the best spherical CLUSTER in a foreground cloud., segment_ball(), solve_rigid(), random_rotation(), Headless tests for central/calibration.py (the wand-calibration math).  Synthe (+14 more)

### Community 12 - "Spatial Depth Denoise"
Cohesion: 0.11
Nodes (18): EXPERIMENTAL — edge-preserving spatial (within-frame) depth smoothing.  The CO, Edge-preserving bilateral smoothing of one depth grid (millimetres).      radi, No-op — this filter is stateless (kept for call-site symmetry with         Temp, depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length         w*h, SpatialDepthFilter, EXPERIMENTAL spatial (within-frame) depth denoise tests (central/spatial_denois, No per-sensor memory: filtering one shape then a different shape must     just, In the relay the temporal filter runs first and hands its uint16 array     stra (+10 more)

### Community 13 - "Simulated Node"
Cohesion: 0.14
Nodes (21): ball_world_pos(), main(), project_keypoints(), Simulated capture node.  Stands in for a real Jetson/x86 node so the whole spi, view = R^T · (p - t) for a view->world pose (R rows, t)., The shared ball trajectory (world frame, metres): a slow Lissajous wave     thr, World-frame joints -> CPOS keypoints for a sensor at `pose` (view->world     R, Ray-render a sphere (center `ball_view` in the VIEW frame: x right,     y up, z (+13 more)

### Community 14 - "Per-Viewer Sender"
Cohesion: 0.18
Nodes (13): ClientSender, Per-viewer sender thread with a latest-frame mailbox.      Every viewer socket, Queue a binary cloud frame; overwrites any unsent one for the sensor., FakeConn, Per-viewer ClientSender tests (headless, no sockets needed).  The fix under te, Stands in for a client socket: records sends, can wedge like a full     TCP buf, While the socket is wedged, newer cloud frames replace unsent ones —     per se, The hand-off must return immediately even while sendall is stuck —     this is (+5 more)

### Community 15 - "Frame Protocol & Relay Ingest"
Cohesion: 0.17
Nodes (15): encode_calib(), Frame, One synchronized depth+color frame. Plain class (no dataclass) so it     import, _depth_frame(), Relay ingest freshness (drop-stale-on-ingest).  The relay's node-reader must s, A tiny valid CVF1 frame (geometry only, stride 1)., Feed pre-built node messages into a fresh reader connection, run     `_serve_no, _run_reader() (+7 more)

### Community 16 - "Recording HTTP Tests"
Cohesion: 0.16
Nodes (15): _http_get(), _payload(), Scene-recording tests (headless): the CPR1 take writer/reader round-trip, the n, Captures the server's broadcast texts without real sockets., record_start/stop/delete via the browser-command entry point, with     frames t, Run one request through _serve_http over a socketpair; returns     (status_line, GET /recordings lists takes; GET /recordings/<id> serves the file     byte-exac, Frames come back verbatim, timestamped, with correct sidecar meta. (+7 more)

### Community 17 - "Background Subtraction"
Cohesion: 0.14
Nodes (9): BackgroundSubtractor, Background-plate subtraction for a static rig.  The camera is fixed, so we can, Begin averaging `frames` frames into a new background plate. Disables         s, Accumulate one frame during capture. Returns True when the plate is         fin, Boolean (H,W) mask: True = keep (closer than background, or background, Background-plate subtraction tests (headless; numpy required).  Run: python3 -, test_capture_and_subtract(), test_clear_disables() (+1 more)

### Community 18 - "LAN Auto-Discovery"
Cohesion: 0.12
Nodes (13): discover_central(), encode_query(), parse_query(), parse_reply(), LAN auto-discovery of the central preview relay (so the node doesn't need a har, Return the rig_id from a query datagram, or None if it isn't one., Return (rig_id, node_port) from a reply datagram, or None if invalid., Answer discovery queries for `rig_id` with this relay's node TCP port.      Sp (+5 more)

### Community 19 - "Relay Unprojection Helpers"
Cohesion: 0.14
Nodes (15): aligned_color_grid(), compute_ray_table(), default_intrinsics(), extract_depth_grid(), gravity_to_view(), load_intrinsics(), main(), Central live-preview server (M2).  Bridges the capture side to the browser: (+7 more)

### Community 20 - "MoveNet Pose Estimator"
Cohesion: 0.18
Nodes (12): _bench(), decode_movenet(), letterbox(), MoveNetEstimator, 2D human pose on the node's color image -> CPOS keypoints (docs/skeleton_pose.md, Single-person MoveNet via onnxruntime. Tolerant of the common export     varian, (H, W, 3) uint8 RGB -> [(joint_id, u, v, conf)] in image pixels.         Per-st, Resize an (H, W, 3) image into a (size, size, 3) letterboxed square     (neares (+4 more)

### Community 21 - "Skeleton Solve Tests"
Cohesion: 0.21
Nodes (13): Solve every sensor's rigid transform into the reference sensor's frame     from, solve_skeleton(), parse_pose(), A synthetic person (world frame, metres): pelvis wanders slowly through     the, yaw_deg,x,y,z[,pitch_deg]' -> (R (3x3 rows), t) — this sensor's     view->world, skeleton_world_joints(), Headless tests for the skeleton/pose pipeline (docs/skeleton_pose.md):    - CP, sim keypoints -> pinhole unprojection (the relay's math with zero     distortio (+5 more)

### Community 22 - "One-Euro Joint Smoothing"
Cohesion: 0.16
Nodes (8): JointSmoother, OneEuro, Per-joint One-Euro smoothing for (u, v) pixels + z metres. A joint     unseen f, One-Euro filter (Casiez et al.) for one scalar channel — the standard     keypo, Jitter shrinks a lot at rest; fast motion tracks with little lag., A low-confidence jump moves the filtered joint much less than the same     jump, test_conf_weighted_smoothing(), test_one_euro()

### Community 23 - "Stationary Ball Sampler"
Cohesion: 0.23
Nodes (4): Consider one frame. Returns 'ok', 'count' or 'fit'.          The ball is SEGME, Stop-and-go wand sampling for UNSYNCED / slow rigs.      Continuous waving pai, Global state machine (called under the lock). One capture per hold:         com, StationaryBallSampler

### Community 24 - "Take Recorder & Demo"
Cohesion: 0.26
Nodes (5): main(), Central recorder.  Accepts a TCP connection from each capture node, reads the, Recorder, main(), End-to-end spine demo (no hardware).  Spins up the central recorder and N simu

### Community 25 - "Pose Worker Thread"
Cohesion: 0.20
Nodes (8): PoseWorker, Runs the estimator OFF the capture path.      submit(color, depth) stashes ref, _FakeEstimator, Person-shaped keypoints with confident torso joints (5/6/11/12)., Weak torso (furniture ghost) -> the frame emits NOTHING., --pose-joints minimal: only the requested joints are emitted., test_pose_worker_joint_subset(), test_pose_worker_person_gate()

### Community 26 - "Frame Wire I/O"
Cohesion: 0.23
Nodes (11): crypt-capture wire protocol — a single synchronized depth+color frame.  One me, Read one node->central message, dispatching on the leading magic.      Returns, Read exactly n bytes from a blocking socket, or b'' on clean EOF., Read one Frame from a socket, or None on clean connection close., read_frame(), read_message(), _recv_exactly(), socket (+3 more)

### Community 27 - "CPV3 Reconstruction Tests"
Cohesion: 0.41
Nodes (11): _blob_depth(), _parse_cpv3(), CPV3 (browser-GPU-unproject wire, approach A / docs/gpu_unproject.md) tests., Mirror the browser's GPU unprojection from a parsed CPV3 frame., _rays(), _reconstruct(), run(), test_cpv3_reconstructs_cpv1_points() (+3 more)

### Community 28 - "XR Pose Passthrough"
Cohesion: 0.29
Nodes (8): _decode_text(), FakeSender, XR pose passthrough tests (headless, no sockets).  The feature under test: a p, A PreviewServer with just the client-side state the passthrough     touches — t, _server_stub(), test_drop_frees_sid_entry(), test_fanout_excludes_sender_and_stamps_sid(), test_sid_stable_per_connection_and_distinct()

### Community 29 - "CPV2 Message Encoding"
Cohesion: 0.35
Nodes (10): build_message(), Serialise one point-cloud frame. `fmt` selects the wire format:     'cpv1' (flo, parse_cpv2(), CPV2 compact wire format: quantised uint16 positions + valid-mask bitmap grid., Reference CPV2 decoder (what the viewer implements). Returns a dict with     de, _sample_cloud(), test_bitmap_matches_unproject_grid(), test_empty_frame() (+2 more)

### Community 30 - "Pose Inference Process"
Cohesion: 0.22
Nodes (6): _pose_child_main(), PoseProcess, PoseWorker in its own PROCESS — the hard-won lesson of this codebase     (see k, Start forwarding the child's [(jid,u,v,z,conf)] lists to emit()., Capture thread: hand the newest frame to the child if it's ready         for on, Child process: build the estimator (CUDA/TensorRT init happens HERE,     never

### Community 31 - "Camera Mode Tables"
Cohesion: 0.22
Nodes (9): apply_camera_command(), clamp_fps(), grid_dims(), max_fps(), Azure Kinect capture-mode tables — pure data, NO pyk4a import.  Both the real, Highest fps both the depth mode and color resolution support., Snap a requested fps down to what the chosen modes actually allow., (width, height) of the streamed point grid for a config — depth res for     col (+1 more)

### Community 32 - "IMU Path Tests"
Cohesion: 0.24
Nodes (5): _FakeSock, IMU / orientation path tests (headless; numpy required for the relay parts)., Minimal recv()-only socket over a fixed byte buffer (for read_message)., test_dispatch_still_reads_calib(), test_imu_roundtrip()

### Community 33 - "Floor Leveling"
Cohesion: 0.28
Nodes (9): fit_floor(), Fit the floor plane in a cloud: the lowest dense band of points along     the u, Per-sensor floor leveling composed onto an existing rig solution.      samples, solve_floor_level(), World-frame scene: floor at y=0, a wall, a body blob., Two cameras with DIFFERENT floor tilts (the user-visible bug: one     global co, synth_room(), test_fit_floor() (+1 more)

### Community 34 - "Control Plane"
Cohesion: 0.25
Nodes (8): encode(), Control channel — central → node commands (the M1 control plane).  The frame s, Encode a command dict to bytes., Read one command dict from a socket, or None on clean close., Spawn a daemon thread that reads commands and calls on_command(dict).      Ret, read_command(), _recv_exactly(), start_reader()

### Community 35 - "Pose Model Tests"
Cohesion: 0.22
Nodes (8): _build_dummy_movenet(), A minimal ONNX with MoveNet's exact interface — NHWC [1,S,S,3] input,     [1,1,, The real estimator path (onnxruntime session, dtype/layout detection,     lette, The out-of-process pose path end-to-end with a real (dummy) ONNX:     child loa, Relay-side pose for nodes that send no CPOS (the weak Nano): the relay     runs, test_central_pose_fallback(), test_movenet_estimator(), test_pose_process()

### Community 36 - "Joint Tracker"
Cohesion: 0.25
Nodes (5): JointTracker, Accumulates per-sensor, PER-JOINT (time, 3D point) tracks from pose     keypoin, joints: iterable of (joint_id, p (3,), conf). Low-confidence or         depth-l, Total joint samples per sensor (for progress display)., test_joint_tracker()

### Community 37 - "IMU Gravity Reading"
Cohesion: 0.25
Nodes (8): _default_accel_to_depth(), _drain_accel(), Azure Kinect DK accelerometer -> depth-camera optical axis convention.      Th, Factory ACCEL->DEPTH rotation via pyk4a's extrinsic getter. The accel is a, Return the FRESHEST accelerometer sample (x,y,z) by draining the IMU FIFO., Freshest GRAVITY (down) unit vector in the depth optical frame (x right,     y, _read_gravity_optical(), _sdk_accel_to_depth()

### Community 38 - "Extrinsic Registration Tests"
Cohesion: 0.29
Nodes (4): object, _FakeSock, grid->depth extrinsic registration tests (headless; numpy for the relay parts)., test_extrinsic_roundtrip()

### Community 39 - "Texture UV Tests"
Cohesion: 0.43
Nodes (7): _fake_reader(), _pinhole_rays(), Textured-mesh data-plane tests (docs/textured_mesh.md): the CCLR/CTEX node mess, run(), test_frame_messages_roundtrip(), test_unproject_uv_recovers_pixels(), test_wire_uv_texture_blocks()

### Community 40 - "Floor Sampler"
Cohesion: 0.29
Nodes (3): FloorSampler, Accumulates a bounded per-sensor sample of RAW view-frame points for     the fl, test_floor_sampler()

### Community 41 - "CPV2/UV Serialization"
Cohesion: 0.29
Nodes (7): _build_message_v2(), _quantize_positions(), Serialise the texture-UV block: count×2 uint16, normalised [0,1]×65535., CPV2 payload: same 20-byte header (magic CPV2) then a 16-byte quant block     (, CPV2 positions: metres float32 -> uint16 with a per-frame offset (the     min c, _uv_block(), test_quant_step_below_noise()

### Community 42 - "Camera Mode Tests"
Cohesion: 0.48
Nodes (6): Camera-mode table + set_camera command tests (headless; no pyk4a needed).  The, run(), test_apply_command_restart_flags(), test_color_resolution_free_in_color_to_depth(), test_fps_clamp(), test_grid_dims_follow_alignment()

### Community 43 - "Pose Gate Hysteresis Tests"
Cohesion: 0.29
Nodes (6): Torso confidence follows a per-call script (chair flicker etc.)., Feed the threaded worker exactly ONE frame and wait for its inference     (+ a, Isolated confident frames (furniture flukes) never acquire; a person     needs, _ScriptedEstimator, _step(), test_pose_worker_gate_hysteresis()

### Community 44 - "Keypoint Depth Sampling"
Cohesion: 0.40
Nodes (3): Depth (metres) under a keypoint: median of the non-zero values in a     (2*half, sample_depth(), test_sample_depth()

### Community 46 - "CPV3/Texture Serialization"
Cohesion: 0.50
Nodes (4): build_message_v3(), Serialise the texture block (LAST): u8 format, u16 w, u16 h, u32 len,     then, CPV3 payload: the browser unprojects (docs/gpu_unproject.md). Same 20-byte, _texture_block()

### Community 47 - "Unproject & Color UV"
Cohesion: 0.50
Nodes (4): _project_color_uv(), Project depth-frame optical points into the COLOUR image → normalised UVs     (, Depth grid -> (xyz, rgb, grid) for the valid (non-zero) pixels, using the     d, unproject()

## Knowledge Gaps
- **28 isolated node(s):** `install-node-service.sh script`, `run-node.sh script`, `update-node.sh script`, `GIT_TERMINAL_PROMPT`, `crypt-capture pipeline` (+23 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PreviewServer` connect `Preview Relay Server` to `Pose Model Tests`, `Temporal Depth Denoise`, `Pose Gate Hysteresis Tests`, `Spatial Depth Denoise`, `Frame Protocol & Relay Ingest`, `Recording HTTP Tests`, `Relay Unprojection Helpers`, `MoveNet Pose Estimator`, `Skeleton Solve Tests`, `Pose Worker Thread`, `XR Pose Passthrough`?**
  _High betweenness centrality (0.164) - this node is a cross-community bridge._
- **Why does `TemporalDepthFilter` connect `Temporal Depth Denoise` to `Preview Relay Server`, `Relay Unprojection Helpers`, `Per-Viewer Sender`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `SpatialDepthFilter` connect `Spatial Depth Denoise` to `Preview Relay Server`, `Relay Unprojection Helpers`, `Per-Viewer Sender`?**
  _High betweenness centrality (0.045) - this node is a cross-community bridge._
- **Are the 9 inferred relationships involving `PreviewServer` (e.g. with `SpatialDepthFilter` and `TemporalDepthFilter`) actually correct?**
  _`PreviewServer` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 4 inferred relationships involving `PoseWorker` (e.g. with `ClientSender` and `PreviewServer`) actually correct?**
  _`PoseWorker` has 4 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ClientSender` (e.g. with `SpatialDepthFilter` and `TemporalDepthFilter`) actually correct?**
  _`ClientSender` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `TemporalDepthFilter` (e.g. with `ClientSender` and `PreviewServer`) actually correct?**
  _`TemporalDepthFilter` has 2 INFERRED edges - model-reasoned connections that need verification._