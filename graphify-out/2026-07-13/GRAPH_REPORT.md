# Graph Report - crypt-capture  (2026-07-13)

## Corpus Check
- 74 files · ~112,183 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1106 nodes · 1674 edges · 167 communities (62 shown, 105 thin omitted)
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 40 edges (avg confidence: 0.54)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `eafef6e9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

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
- JointTracker
- Node Launch Script
- Node Auto-Update Script
- Calibration Dump
- Core Design Principles
- Node Service Install
- Compute Bottleneck Rationale
- Node hardware — findings & recommendation
- README.md
- README.md
- central/recorder.py (synced take writer)
- crypt-capture pipeline
- North Star — indistinguishable real-time vs prerecorded WebXR
- Worker processes, not threads (GIL convoy avoidance)
- One shared world coordinate frame
- Live stream == recorded clip representation
- A-lite (relay decodes, browser unprojects)
- Browser-GPU unprojection (approach A)
- CPV3 wire format (depth + bitmap + calib)
- CPV3 vertex-shader unproject (GpuPointCloud)
- Full-A (browser RVL decode + shader denoise)
- Numba RVL decode (approach C, ~10x)
- Ray-table texture (baked undistortion)
- PCVR vs standalone: per-client LOD, one architecture
- WebTransport/WebRTC transport (approach D)
- Azure Kinect Body Tracking not on ARM/Jetson
- Jetson node viability (fiddly for Azure Kinect)
- Orbbec Femto Bolt (Kinect successor)
- Jetson Orin Nano has no NVENC
- RVL paper (Wilson, ISS 2017)
- Sensor Stream Pipe (Moetsi) prior art
- x86 mini-PC + small NVIDIA GPU node (lowest risk)
- Kinect cold-boot enumeration (power-up ordering)
- Depth engine needs GL context (error 204)
- JetPack 6.2 / Ubuntu 22.04 / Python 3.10
- libsoundio1 gotcha (pull 20.04 .deb)
- MoveNet Thunder ONNX + onnxruntime-gpu
- numpy<2 requirement (pyk4a NumPy 1.x ABI)
- k4a udev rules (99-k4a.rules, required)
- Auto-update on boot (push → reboot → runs latest)
- LAN discovery (--host auto, rig id)
- kinect-node systemd service (Restart=always supervisor)
- Cross-sensor outlier removal (merged cloud)
- Flying-pixel / edge-artifact removal
- Continuous micro-recalibration (ICP drift correction)
- Heavier offline bake pass for saved takes
- Per-point surface normals from depth gradient
- Photometric color harmonization across sensors
- Recorder tees exact broadcast bytes
- Spatial depth denoise (edge-preserving bilateral)
- Temporal depth denoise (One-Euro, per-pixel)
- View-angle-weighted overlap blending
- Browser to server control plane
- CPR1 take container
- CPV1 preview frame format
- CPV2 compact wire format
- CPV3 browser-GPU unproject format
- HTTP /recordings endpoints
- rig_poses / calib_status messages
- Scene recording (CPR1 take)
- sensor_calib JSON message
- set_camera command
- CCAL intrinsics handshake
- CVF1 frame message
- Recorded take on-disk layout
- MVP milestones M0-M5
- WebXR edge-of-reality North Star
- Orin vs Nano evaluation
- Real-time capture web app architecture
- One shared world coordinate frame
- Source-agnostic representation principle
- Browser transport choice (WebSocket v0)
- ChArUco board rejected
- Per-sensor floor leveling
- ICP rejected for inward rig
- Marker-ball (wand) calibration
- segment_ball spherical cluster detection
- Robust RANSAC rigid solve
- Stop-and-go wand procedure
- Tier-1 rough calibration
- Tier-2 fine wand pass
- Two-tier alignment (rough + fine)
- CPOS pose keypoint wire message
- Inference on node, central fallback
- Pose model choice (RTMPose/MoveNet)
- MoveNetEstimator ONNX
- Person gate + hysteresis
- Skeleton / pose pipeline
- Skeleton-based rig alignment
- PoseWorker / PoseProcess
- CCLR colour calibration message
- CTEX JPEG texture message
- MeshCloud viewer texturing
- Textured mesh (full-res colour on cheap geometry)
- Relay UV projection
- Per-view AI matting (RVM / BackgroundMattingV2)
- glTF + meshopt web delivery (not Draco)
- Hardware frame sync via 3.5mm daisy-chain
- Network trigger (arm/record/stop)
- NVENC H.26x color transport
- One Kinect per edge node
- RVL lossless depth codec
- SMPL-X template tracking (approach C)
- TSDF fusion → watertight mesh sequence (approach B)
- test_solve_floor_level
- _read_gravity_optical
- object
- test_relay_workers.py
- test_pose_worker_gate_hysteresis
- test_recording.py
- sample_depth
- test_xr_pose.py
- _run_ring_pass
- mesh_take.py
- preview_client.py
- disable-wifi-powersave.sh

## God Nodes (most connected - your core abstractions)
1. `PreviewServer` - 71 edges
2. `run()` - 21 edges
3. `ClientSender` - 20 edges
4. `SceneBundler` - 20 edges
5. `PoseWorker` - 20 edges
6. `Frame` - 20 edges
7. `TemporalDepthFilter` - 19 edges
8. `run()` - 18 edges
9. `SpatialDepthFilter` - 17 edges
10. `BackgroundSubtractor` - 17 edges

## Surprising Connections (you probably didn't know these)
- `_FakeEstimator` --uses--> `JointTracker`  [INFERRED]
  tests/test_pose.py → central/calibration.py
- `_ScriptedEstimator` --uses--> `JointTracker`  [INFERRED]
  tests/test_pose.py → central/calibration.py
- `ClientSender` --uses--> `MoveNetEstimator`  [INFERRED]
  central/preview_server.py → node/pose.py
- `ClientSender` --uses--> `PoseWorker`  [INFERRED]
  central/preview_server.py → node/pose.py
- `SceneBundler` --uses--> `MoveNetEstimator`  [INFERRED]
  central/preview_server.py → node/pose.py

## Import Cycles
- None detected.

## Communities (167 total, 105 thin omitted)

### Community 0 - "Preview Relay Server"
Cohesion: 0.05
Nodes (25): Sensors whose sample is committed into the CURRENT capture (empty         once, gravity_to_view(), main(), PreviewServer, Read browser messages: text = a JSON command to forward to nodes;         also, Fan a presenting viewer's headset/controller pose out to every         OTHER vi, Send a control command to every connected node. Returns count sent., Re-express a gravity (down) unit vector from the depth OPTICAL frame     (x rig (+17 more)

### Community 2 - "RVL Codec & Meshing"
Cohesion: 0.07
Nodes (35): frame_to_mesh(), load_calib(), main(), mesh_frame(), Convert a recorded take into per-frame triangle meshes (PLY) you can view.  Th, Unproject a depth grid to a triangle mesh with a depth-discontinuity cut., write_ply(), compress() (+27 more)

### Community 3 - "Hardware & Data-Quality Strategy"
Cohesion: 0.11
Nodes (18): 💡 Continuous micro-recalibration (drift correction), 💡 Cross-sensor outlier removal, 💡 Flying-pixel / edge-artifact removal, 💡 Heavier offline "bake" pass for saved takes, If picking a next step (informal priority read, not a decision), Keep this file current, Kinect data quality — relay post-processing catalog, Multi-sensor / seam quality (the "make the subject look whole" cluster) (+10 more)

### Community 4 - "Temporal Depth Denoise"
Cohesion: 0.09
Nodes (23): EXPERIMENTAL — per-pixel temporal One-Euro filter over the raw depth grid.  Ki, depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length         w*h, Per-sensor, per-pixel One-Euro low-pass over raw depth (millimetres).      min, Drop filter memory (a sensor's, or all). Call when a sensor's         camera mo, _SensorState, TemporalDepthFilter, EXPERIMENTAL temporal depth denoise tests (central/temporal_denoise.py).  Head, A pixel that goes invalid for a long gap (subject moved away) and then     beco (+15 more)

### Community 5 - "Rough Rig Solve"
Cohesion: 0.12
Nodes (29): fit_floor(), level_rotation(), load_rig_calib(), Rotation taking a measured view-frame gravity (down) unit vector onto     world, Best yaw (rotation about +Y) + translation mapping A onto B (N,3 each,     alre, Tier-1 rough rig solve (zero props, ~5-10 cm expected).      tracks: {sensor_i, Fit the floor plane in a cloud: the lowest dense band of points along     the u, Per-sensor floor leveling composed onto an existing rig solution.      samples (+21 more)

### Community 6 - "WebSocket & Client Tools"
Cohesion: 0.07
Nodes (32): accept_key(), client_handshake(), encode_frame(), Minimal WebSocket (RFC 6455) helpers — stdlib only, no dependencies.  Just eno, Encode one WS frame (FIN=1). Mask only for client→server frames., Read one WS frame. Returns (opcode, payload) or None on EOF/close., Compute the Sec-WebSocket-Accept value for a client's key., Read an HTTP request/response head (up to the blank line). (+24 more)

### Community 7 - "Scene Recording"
Cohesion: 0.08
Nodes (18): delete_recording(), list_recordings(), Scene recording: capture the LIVE preview stream to disk, replayable in the vie, Tee one outgoing CPV1 message into the active take. Non-blocking:         store, Live stats for the record_status broadcast (None when idle)., Finish the active take: drain the queue, write the sidecar, return         the, Writer thread: drain the queue to disk until stop() drains us dry.         Also, Yield (t, payload) for every frame of a `.cpr` take. Raises ValueError     on a (+10 more)

### Community 8 - "Kinect Node Pipeline"
Cohesion: 0.09
Nodes (18): BackgroundSubtractor, erode_mask(), foreground_mask(), Background-plate subtraction for a static rig.  The camera is fixed, so we can, Foreground mask against a *snapshotted* plate (see foreground()). Module-     l, Erode a boolean foreground mask by `px` pixels: a kept pixel survives     only, Begin averaging `frames` frames into a new background plate. Disables         s, Boolean (H,W) mask: True = keep (closer than background, or background (+10 more)

### Community 9 - "Jetson Setup & GPU-Unproject"
Cohesion: 0.12
Nodes (16): 1. Flash the OS, 1b. Remote access (SSH) + the Xorg/GL session, 2. Set the Orin to max performance (it has real headroom now), 3. USB buffer + udev (same as the Nano), 4. Azure Kinect SDK + depth engine — the hard part on 22.04 (aarch64), 5. Smoke-test the sensor, 6. Python + deps, 7. Get the code + run as a service (+8 more)

### Community 10 - "Rig Calibration Solver"
Cohesion: 0.21
Nodes (8): BallTracker, Accumulates per-sensor (time, ball-center) tracks for the Tier-2 wand     pass., collect(), main(), parse_positions(), Rig extrinsic calibration — the marker-ball ("wand") collection + solve.  Head, CPV1 binary message -> (sensor_id, positions (N,3) float32) or None., Feed the tracker from the live stream for `seconds`. Arrival time is the     co

### Community 11 - "Ball Fit & Calibration Tests"
Cohesion: 0.15
Nodes (22): fit_sphere(), Rigid transform (R, t) minimising |R·A + t - B|^2 (Kabsch/Umeyama).      A, B:, Center of a sphere of KNOWN radius fitted to surface points (N,3).      Gauss-, Find the marker ball as the best spherical CLUSTER in a foreground cloud., segment_ball(), solve_rigid(), random_rotation(), Headless tests for central/calibration.py (the wand-calibration math).  Synthe (+14 more)

### Community 12 - "Spatial Depth Denoise"
Cohesion: 0.11
Nodes (18): EXPERIMENTAL — edge-preserving spatial (within-frame) depth smoothing.  The CO, Edge-preserving bilateral smoothing of one depth grid (millimetres).      radi, No-op — this filter is stateless (kept for call-site symmetry with         Temp, depth_u16: bytes-like (buffer-protocol) row-major uint16 mm, length         w*h, SpatialDepthFilter, EXPERIMENTAL spatial (within-frame) depth denoise tests (central/spatial_denois, No per-sensor memory: filtering one shape then a different shape must     just, In the relay the temporal filter runs first and hands its uint16 array     stra (+10 more)

### Community 13 - "Simulated Node"
Cohesion: 0.12
Nodes (25): ball_world_pos(), main(), parse_pose(), project_keypoints(), Simulated capture node.  Stands in for a real Jetson/x86 node so the whole spi, yaw_deg,x,y,z[,pitch_deg]' -> (R (3x3 rows), t) — this sensor's     view->world, view = R^T · (p - t) for a view->world pose (R rows, t)., The shared ball trajectory (world frame, metres): a slow Lissajous wave     thr (+17 more)

### Community 14 - "Per-Viewer Sender"
Cohesion: 0.14
Nodes (14): ClientSender, Per-viewer sender thread with a latest-frame mailbox.      Every viewer socket, Queue a binary cloud frame; overwrites any unsent one for the sensor., Queue a BUNDLE of (sensor_id, ws_frame) under one lock, so the         sender t, FakeConn, Per-viewer ClientSender tests (headless, no sockets needed).  The fix under te, Stands in for a client socket: records sends, can wedge like a full     TCP buf, While the socket is wedged, newer cloud frames replace unsent ones —     per se (+6 more)

### Community 15 - "Frame Protocol & Relay Ingest"
Cohesion: 0.29
Nodes (9): _depth_frame(), Relay ingest freshness (drop-stale-on-ingest).  The relay's node-reader must s, A tiny valid CVF1 frame (geometry only, stride 1)., Feed pre-built node messages into a fresh reader connection, run     `_serve_no, message_buffered (the drop-stale gate) must say False for a PARTIALLY     arriv, _run_reader(), test_drops_stale_keeps_newest(), test_partial_message_does_not_trigger_skip() (+1 more)

### Community 16 - "Recording HTTP Tests"
Cohesion: 0.12
Nodes (23): _apply_color_controls(), _build_config(), _depth_to_color_extrinsic(), _grid_to_depth_extrinsic(), main(), parse_imu_axes(), Real Azure Kinect capture node — drop-in replacement for node/sim_node.py.  Ca, Parse a manual IMU->depth axis remap like "-y,-x,-z" into a function     (x,y,z (+15 more)

### Community 17 - "Background Subtraction"
Cohesion: 0.25
Nodes (9): Presentation-coherent broadcast: hold each sensor's freshest finished     frame, SceneBundler, _Collector, SceneBundler (relay scene-coherent broadcast) behaviour.  The bundler holds each, test_complete_bundle_flushes_before_timeout(), test_dead_sensor_leaves_barrier(), test_missing_sensor_flushes_at_timeout(), test_newer_frame_overwrites_pending() (+1 more)

### Community 18 - "LAN Auto-Discovery"
Cohesion: 0.12
Nodes (13): discover_central(), encode_query(), parse_query(), parse_reply(), LAN auto-discovery of the central preview relay (so the node doesn't need a har, Return the rig_id from a query datagram, or None if it isn't one., Return (rig_id, node_port) from a reply datagram, or None if invalid., Answer discovery queries for `rig_id` with this relay's node TCP port.      Sp (+5 more)

### Community 19 - "Relay Unprojection Helpers"
Cohesion: 0.06
Nodes (44): build_message(), _build_message_v2(), build_message_v3(), extract_depth_grid(), _project_color_uv(), _quantize_positions(), Heavy STATELESS stage: unproject -> per-sensor rig transform ->         CPV1 me, Project depth-frame optical points into the COLOUR image → normalised UVs     ( (+36 more)

### Community 20 - "MoveNet Pose Estimator"
Cohesion: 0.15
Nodes (14): _bench(), decode_movenet(), letterbox(), MoveNetEstimator, 2D human pose on the node's color image -> CPOS keypoints (docs/skeleton_pose.md, Single-person MoveNet via onnxruntime. Tolerant of the common export     varian, (H, W, 3) uint8 RGB -> [(joint_id, u, v, conf)] in image pixels.         Per-st, Resize an (H, W, 3) image into a (size, size, 3) letterboxed square     (neares (+6 more)

### Community 21 - "Skeleton Solve Tests"
Cohesion: 0.14
Nodes (14): aligned_color_grid(), compute_ray_table(), default_intrinsics(), load_intrinsics(), Central live-preview server (M2).  Bridges the capture side to the browser:, Rough pinhole intrinsics from horizontal FOV (Azure Kinect NFOV ≈ 75°).      G, Per-pixel viewing rays (X/Z, Y/Z) for a full-res grid, applying the     Brown-C, Per-sensor (fx,fy,cx,cy,dist): the node's own (sent on connect) win;         el (+6 more)

### Community 22 - "One-Euro Joint Smoothing"
Cohesion: 0.13
Nodes (10): JointSmoother, OneEuro, Per-joint One-Euro smoothing for (u, v) pixels + z metres. A joint     unseen f, One-Euro filter (Casiez et al.) for one scalar channel — the standard     keypo, object, _FakeSock, Jitter shrinks a lot at rest; fast motion tracks with little lag., A low-confidence jump moves the filtered joint much less than the same     jump (+2 more)

### Community 23 - "Stationary Ball Sampler"
Cohesion: 0.23
Nodes (4): Consider one frame. Returns 'ok', 'count' or 'fit'.          The ball is SEGME, Stop-and-go wand sampling for UNSYNCED / slow rigs.      Continuous waving pai, Global state machine (called under the lock). One capture per hold:         com, StationaryBallSampler

### Community 24 - "Take Recorder & Demo"
Cohesion: 0.26
Nodes (5): main(), Central recorder.  Accepts a TCP connection from each capture node, reads the, Recorder, main(), End-to-end spine demo (no hardware).  Spins up the central recorder and N simu

### Community 25 - "Pose Worker Thread"
Cohesion: 0.18
Nodes (10): PoseWorker, Runs the estimator OFF the capture path.      submit(color, depth) stashes ref, _FakeEstimator, Person-shaped keypoints with confident torso joints (5/6/11/12)., Latest-frame semantics + depth attach + emit payloads., Weak torso (furniture ghost) -> the frame emits NOTHING., --pose-joints minimal: only the requested joints are emitted., test_pose_worker() (+2 more)

### Community 26 - "Frame Wire I/O"
Cohesion: 0.13
Nodes (10): Frame message (TCP, little-endian) — see `protocol/frame.py`, Intrinsics handshake (TCP, node → central) — `protocol/frame.py`, Take on disk — see `central/recorder.py`, Wire protocol & take format, main(), NodeProbe, Find WHERE the per-sensor stream dips live: radio link vs node vs shared.  Run o, Continuously measure one node's reachability + RTT (TCP connect). (+2 more)

### Community 27 - "CPV3 Reconstruction Tests"
Cohesion: 0.42
Nodes (12): _blob_depth(), _parse_cpv3(), CPV3 (browser-GPU-unproject wire, approach A / docs/gpu_unproject.md) tests., Mirror the browser's GPU unprojection from a parsed CPV3 frame., _rays(), _reconstruct(), run(), test_cpv3_carries_rgb() (+4 more)

### Community 28 - "XR Pose Passthrough"
Cohesion: 0.12
Nodes (16): 0. What you need, 10. Skeleton pose — GPU inference (one-time per Orin), 11. Cold-boot power ordering (know this), 12. Validate end-to-end, 1. Flash JetPack 6.2, 2. Remote access (SSH) — go headless after this, 3. Xorg + autologin (the depth engine needs a GL context), 4. Performance (+8 more)

### Community 29 - "CPV2 Message Encoding"
Cohesion: 0.12
Nodes (15): 1. OS, 2. Azure Kinect SDK + depth engine (the fiddly part), 3. USB permissions, 4. Smoke test the sensor, 5. Python, 6. Get the code on the Nano, 7. Run (single sensor), 8. Verify the real take (+7 more)

### Community 30 - "Pose Inference Process"
Cohesion: 0.22
Nodes (6): _pose_child_main(), PoseProcess, PoseWorker in its own PROCESS — the hard-won lesson of this codebase     (see k, Start forwarding the child's [(jid,u,v,z,conf)] lists to emit()., Capture thread: hand the newest frame to the child if it's ready         for on, Child process: build the estimator (CUDA/TensorRT init happens HERE,     never

### Community 31 - "Camera Mode Tables"
Cohesion: 0.22
Nodes (9): apply_camera_command(), clamp_fps(), grid_dims(), max_fps(), Azure Kinect capture-mode tables — pure data, NO pyk4a import.  Both the real, Highest fps both the depth mode and color resolution support., Snap a requested fps down to what the chosen modes actually allow., (width, height) of the streamed point grid for a config — depth res for     col (+1 more)

### Community 32 - "IMU Path Tests"
Cohesion: 0.14
Nodes (13): Downstream: server → browser JSON (text messages), HTTP endpoints (same port as the WebSocket), Live preview protocol (central → browser), Message: `CPV1` (PreviewFrame), little-endian, Message: `CPV2` (compact wire format, little-endian), Message: `CPV3` (browser-GPU unproject, little-endian), Scene recording (record the live stream, replay it in the scene), `sensor_calib` (server → browser JSON, for CPV3) (+5 more)

### Community 33 - "Floor Leveling"
Cohesion: 0.15
Nodes (12): CLAUDE.md — project context & handoff, Current status (what's DONE and validated), Environment gotchas (learned the hard way on the Nano), graphify, How to run, Open items, Rendering R&D already done (in the `crypt` repo), Repo layout (+4 more)

### Community 34 - "Control Plane"
Cohesion: 0.25
Nodes (8): encode(), Control channel — central → node commands (the M1 control plane).  The frame s, Encode a command dict to bytes., Read one command dict from a socket, or None on clean close., Spawn a daemon thread that reads commands and calls on_command(dict).      Ret, read_command(), _recv_exactly(), start_reader()

### Community 35 - "Pose Model Tests"
Cohesion: 0.24
Nodes (7): _CapturingTracker, _fed_cloud(), _frame(), Calibration is fed the RAW (pre-rig) cloud — regression test for the fine-after-, Minimal stand-in for a ball tracker: records every fed cloud and exposes     the, Push one frame through the relay reader with a fine session active and the     g, test_calibration_fed_raw_cloud()

### Community 36 - "Joint Tracker"
Cohesion: 0.39
Nodes (8): _fake_reader(), _pinhole_rays(), Textured-mesh data-plane tests (docs/textured_mesh.md): the CCLR/CTEX node mess, run(), test_frame_messages_roundtrip(), test_take_texture_staleness_window(), test_unproject_uv_recovers_pixels(), test_wire_uv_texture_blocks()

### Community 37 - "IMU Gravity Reading"
Cohesion: 0.15
Nodes (12): Browser, Central (web app server), Components, Feasibility: the network is not the bottleneck — compute is, MVP milestones (single camera, live-preview first), Node (Jetson), North Star (where this is heading), Open questions / risks (+4 more)

### Community 38 - "Extrinsic Registration Tests"
Cohesion: 0.15
Nodes (12): Accuracy: sphere (depth) vs ChArUco (color) vs IR — the honest math, Per-sensor floor leveling (`calibrate_floor`, "floor" tier), Related future work (noted here so the design accounts for it), Rig extrinsic calibration — the marker-ball ("wand") procedure, Status, The calibration ball (what to build), The fine (Tier-2) wand procedure — operator's view (STOP & GO, default), The math (implemented + unit-tested) (+4 more)

### Community 39 - "Texture UV Tests"
Cohesion: 0.18
Nodes (14): denoise_mask(), Remove isolated speckles from a boolean foreground mask: drop any kept     pixe, _encode_color_bbox_jpeg(), _ir_to_gray(), _process_frame(), Masked IR values (uint16 1-D) -> uint8 grey, sqrt tone map with `scale`     as, Encode the ALIGNED colour image, cropped to the valid-pixel bounding     box, a, Per-frame mask -> denoise -> RVL (+ foreground colour pick), the CPU-heavy (+6 more)

### Community 40 - "Floor Sampler"
Cohesion: 0.29
Nodes (3): FloorSampler, Accumulates a bounded per-sensor sample of RAW view-frame points for     the fl, test_floor_sampler()

### Community 41 - "CPV2/UV Serialization"
Cohesion: 0.17
Nodes (11): Central + viewer (implemented), Enabling it on a Jetson (Orin, JetPack 6), GPU inference (the real fps fix), Headless testing (implemented), Model choice (decision), Node inference worker (implemented — `node/pose.py`), Quality knobs (first-hardware findings, 2026-07-03), Remaining (+3 more)

### Community 42 - "Camera Mode Tests"
Cohesion: 0.17
Nodes (15): pair_tracks(), Rig extrinsic calibration from a tracked marker ball (the "wand" pass).  Why t, Outlier-robust rigid solve (RANSAC around solve_rigid).      The wand pass WIL, Pair two (time, point) tracks by nearest timestamp.      track_*: sequences of, Solve every sensor's rigid transform into a reference sensor's frame.      tra, Solve every sensor's rigid transform into the reference sensor's frame     from, # NOTE: on a FINE (wand) calibrated rig the floors are already coplanar to, rig_to_dict() (+7 more)

### Community 43 - "Pose Gate Hysteresis Tests"
Cohesion: 0.18
Nodes (10): Browser (crypt) shader plan, Browser-GPU unprojection ("approach A") — design, Effort / risks / open questions, Migration / compatibility, Sequencing, Tiers: PCVR vs standalone — one architecture, per-client LOD, Two variants — we are building A-lite, What stays on the relay (+2 more)

### Community 44 - "Keypoint Depth Sampling"
Cohesion: 0.25
Nodes (7): Node → relay protocol additions (`protocol/frame.py`), Relay → viewer wire additions (`CPV1`, additive blocks), Status / rollout, Textured mesh — full-resolution colour on cheap geometry, Viewer (`crypt`): `MeshCloud` texturing, Where each piece runs, Why not just make `depth_to_color`/CPV2 fast enough?

### Community 45 - "Grid Block Tests"
Cohesion: 0.24
Nodes (5): _FakeSock, IMU / orientation path tests (headless; numpy required for the relay parts)., Minimal recv()-only socket over a fixed byte buffer (for read_message)., test_dispatch_still_reads_calib(), test_imu_roundtrip()

### Community 46 - "CPV3/Texture Serialization"
Cohesion: 0.29
Nodes (6): Architecture, crypt-capture, Key decisions (and why), Roadmap, Run the spine (no hardware), Status

### Community 47 - "JointTracker"
Cohesion: 0.25
Nodes (5): JointTracker, Accumulates per-sensor, PER-JOINT (time, 3D point) tracks from pose     keypoin, joints: iterable of (joint_id, p (3,), conf). Low-confidence or         depth-l, Total joint samples per sensor (for progress display)., test_joint_tracker()

### Community 58 - "Node hardware — findings & recommendation"
Cohesion: 0.33
Nodes (5): Depth compression: RVL, Node hardware — findings & recommendation, Prior art to evaluate before writing more transport, Sources, Verdict

### Community 155 - "test_solve_floor_level"
Cohesion: 0.17
Nodes (13): _decode_jpeg_rgb(), jpeg_color_grid(), JPEG bytes -> (h,w,3) uint8 RGB array, or None (no codec/corrupt)., FLAG_COLOR_JPEG payload (u16 x0,y0,bw,bh + JPEG of the bbox crop of the     ali, _encode_jpeg(), Encode a BGRA image to JPEG bytes for the textured mesh. Uses OpenCV     (libjp, A subject blob of valid depth + a smooth BGRA colour gradient (smooth     like a, The bbox crop blacks out non-subject pixels (a busy room inside an     inflated (+5 more)

### Community 156 - "_read_gravity_optical"
Cohesion: 0.48
Nodes (6): Camera-mode table + set_camera command tests (headless; no pyk4a needed).  The, run(), test_apply_command_restart_flags(), test_color_resolution_free_in_color_to_depth(), test_fps_clamp(), test_grid_dims_follow_alignment()

### Community 157 - "object"
Cohesion: 0.25
Nodes (8): _default_accel_to_depth(), _drain_accel(), Azure Kinect DK accelerometer -> depth-camera optical axis convention.      Th, Factory ACCEL->DEPTH rotation via pyk4a's extrinsic getter. The accel is a, Return the FRESHEST accelerometer sample (x,y,z) by draining the IMU FIFO., Freshest GRAVITY (down) unit vector in the depth optical frame (x right,     y, _read_gravity_optical(), _sdk_accel_to_depth()

### Community 158 - "test_relay_workers.py"
Cohesion: 0.22
Nodes (12): Frame, crypt-capture wire protocol — a single synchronized depth+color frame.  One me, Read one Frame from a socket, or None on clean connection close., Read one node->central message, dispatching on the leading magic.      Returns, One synchronized depth+color frame. Plain class (no dataclass) so it     import, Read exactly n bytes from a blocking socket, or b'' on clean EOF., read_frame(), read_message() (+4 more)

### Community 159 - "test_pose_worker_gate_hysteresis"
Cohesion: 0.29
Nodes (6): Torso confidence follows a per-call script (chair flicker etc.)., Feed the threaded worker exactly ONE frame and wait for its inference     (+ a, Isolated confident frames (furniture flukes) never acquire; a person     needs, _ScriptedEstimator, _step(), test_pose_worker_gate_hysteresis()

### Community 160 - "test_recording.py"
Cohesion: 0.43
Nodes (6): encode_calib(), _capture(), _frame(), Relay per-sensor worker pool (--workers) equivalence.  --workers > 1 fans the, Run `frames` through a relay reader at the given worker count with     recordin, test_parallel_matches_sequential()

### Community 161 - "sample_depth"
Cohesion: 0.21
Nodes (12): _build_dummy_movenet(), Headless tests for the skeleton/pose pipeline (docs/skeleton_pose.md):    - CP, A minimal ONNX with MoveNet's exact interface — NHWC [1,S,S,3] input,     [1,1,, The out-of-process pose path end-to-end with a real (dummy) ONNX:     child loa, Relay-side pose for nodes that send no CPOS (the weak Nano): the relay     runs, Two posed sensors watch the same moving joints (with per-view noise ~     real, rot_x(), rot_y() (+4 more)

### Community 162 - "test_xr_pose.py"
Cohesion: 0.33
Nodes (3): CentroidTracker, Accumulates per-sensor (time, foreground-centroid) tracks for the Tier-1     ro, test_centroid_tracker()

### Community 163 - "_run_ring_pass"
Cohesion: 0.29
Nodes (7): Accumulate one frame during capture. Returns True when the plate is         fin, n cameras on a circle, 360/n deg apart, looking inward: (R, t) with     p_senso, Feed a 3-camera 120-deg ring where sensor 2 settles LATE at every hold     (the, A camera that settles AFTER the quorum commit must still land in the     same c, _ring_poses(), _run_ring_pass(), test_stationary_late_join()

### Community 164 - "mesh_take.py"
Cohesion: 0.40
Nodes (3): Depth (metres) under a keypoint: median of the non-zero values in a     (2*half, sample_depth(), test_sample_depth()

## Knowledge Gaps
- **205 isolated node(s):** `install-node-service.sh script`, `run-node.sh script`, `update-node.sh script`, `GIT_TERMINAL_PROMPT`, `What this project is` (+200 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **105 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `PreviewServer` connect `Preview Relay Server` to `test_recording.py`, `sample_depth`, `Pose Model Tests`, `Temporal Depth Denoise`, `WebSocket & Client Tools`, `Spatial Depth Denoise`, `Frame Protocol & Relay Ingest`, `Relay Unprojection Helpers`, `MoveNet Pose Estimator`, `Skeleton Solve Tests`, `Pose Worker Thread`, `test_pose_worker_gate_hysteresis`?**
  _High betweenness centrality (0.139) - this node is a cross-community bridge._
- **Why does `SpatialDepthFilter` connect `Spatial Depth Denoise` to `Preview Relay Server`, `Background Subtraction`, `Skeleton Solve Tests`, `Per-Viewer Sender`?**
  _High betweenness centrality (0.039) - this node is a cross-community bridge._
- **Why does `ClientSender` connect `Per-Viewer Sender` to `Preview Relay Server`, `Temporal Depth Denoise`, `Spatial Depth Denoise`, `MoveNet Pose Estimator`, `Skeleton Solve Tests`, `Pose Worker Thread`?**
  _High betweenness centrality (0.036) - this node is a cross-community bridge._
- **Are the 10 inferred relationships involving `PreviewServer` (e.g. with `SpatialDepthFilter` and `TemporalDepthFilter`) actually correct?**
  _`PreviewServer` has 10 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ClientSender` (e.g. with `SpatialDepthFilter` and `TemporalDepthFilter`) actually correct?**
  _`ClientSender` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `SceneBundler` (e.g. with `SpatialDepthFilter` and `TemporalDepthFilter`) actually correct?**
  _`SceneBundler` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `PoseWorker` (e.g. with `ClientSender` and `PreviewServer`) actually correct?**
  _`PoseWorker` has 5 INFERRED edges - model-reasoned connections that need verification._