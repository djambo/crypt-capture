# CLAUDE.md — project context & handoff

> This file is auto-loaded by Claude Code. It distills a long design+build
> session so any new session can continue without re-deriving everything.

> **⚠️ KEEP THIS FILE CURRENT (instruction to Claude).** This is a living
> document — it is only useful if it stays accurate. Whenever a session changes
> something that future-you would need to know, **update this file as part of
> that same work, before ending the turn.** That includes: a decision made or
> reversed; a phase/status changing (e.g. a roadmap item completed); the repo
> layout, run commands, or architecture changing; a new environment gotcha
> discovered; or an open item resolved. Prefer editing the relevant section over
> appending. If a decision here is contradicted by newer reality, fix it (don't
> leave both). Keep it concise — distill, don't dump. A stale CLAUDE.md is worse
> than none, so treat updating it as part of "done," not an afterthought.

## What this project is

A **live, networked capture web app** for volumetric video of a single
performer, captured with **4 Azure Kinect DK** sensors (1 per Jetson). The
central web app connects to the remote Jetsons over Ethernet, **live-streams
their point clouds in real time**, and on a **trigger** has every node
**record a full-fidelity clip to its own local disk** for **download** and
post-processing. End-state = 4 sensors fused into one aligned cloud/surface;
the recorded clips also feed the creative renderer (particles/FX are a goal).

> **Scope note (2026-06):** the product is the *real-time app*; recording is one
> mode of it, not the whole thing. Recording is **local-on-node, downloaded
> after** (the wire only carries live preview + the download). Build order is
> **live preview first**, then record/download, then N nodes, then multi-view
> alignment/fusion. Full architecture + feasibility (bandwidth math, transport
> choices, Orin-vs-Nano eval, MVP milestones) live in
> **`docs/realtime_architecture.md`** — read it before building.

## Vision / North Star (the final goal — build toward it)

**crypt** is an "edge of reality" framework. The end goal is a **WebXR/VR
experience** where the user **cannot tell prerecorded from real-time** volumetric
content sharing the same space:

- **prerecorded** 4-Kinect volumetric clips, AND
- **real-time** volumetric streams of whoever physically enters the capture
  volume — including **the user's own body**: pre-record yourself, then **step
  out of your own body** and watch the clip play back where you stood.
- Far future: stream between **two locations** → volumetric **teleportation**.

Two consequences this North Star forces *early* (don't defer them to fusion):

1. **One shared world coordinate frame is foundational.** Live and recorded must
   register to the same metric space (that's what makes "step out of your body"
   work, even with one rig). The *coordinate-frame* part of calibration is
   MVP-adjacent; only the 4-sensor seam-dissolving **fusion** is later.
2. **Live stream == recorded clip representation.** The renderer should be
   source-agnostic — it plays "point-cloud frames in world space" regardless of
   whether they arrive over the wire (live) or off disk (recorded). Honor this
   from the MVP (record in the wire/take format).

WebXR tightens the **live** path's latency budget (motion-to-photon) → pushes
toward WebRTC/WebTransport over WebSocket eventually; the *prerecorded-in-VR*
path has no such constraint (local playback). Near-term order: **embedded
Kinects streaming + trigger-record → WebXR → remote-location streaming.**

Two repos:
- **`crypt`** — the three.js (r148) creative renderer + rendering R&D (the
  front-end / "how it looks" layer).
- **`crypt-capture`** (this repo) — capture → clean → reconstruct → compress →
  deliver pipeline (the "how the data is made" layer).

## Current status (what's DONE and validated)

- ✅ **Phase 1 spine** (hardware-independent): `protocol/rvl.py` (lossless RVL
  depth codec, ~14×, tested), `protocol/frame.py` (synced depth+color wire
  protocol), `node/sim_node.py` (simulated node), `central/recorder.py` (groups
  sensors by hardware-synced `frame_id`, writes "takes"), `scripts/run_demo.py`.
- ✅ **Real capture validated** on a 1st-gen **Jetson Nano** with one Azure
  Kinect: `node/kinect_node.py` (pyk4a → depth range-clip mask → RVL → stream)
  recorded a real 60-frame take (`takes/real1`, ~131k valid depth px/frame).
  Slow (~1 fps) because RVL is pure-Python on a weak CPU — fine for validation.
- ✅ **Depth-grid meshing**: `node/dump_calibration.py` (depth intrinsics) +
  `processing/mesh_take.py` (unproject depth grid → triangulate with a
  depth-discontinuity edge cut → PLY). Produces a real single-view mesh of the
  subject.
- ✅ **M0 — Fast RVL** (`protocol/rvl.py`): vectorized NumPy `compress`/
  `decompress` alongside the pure-Python reference, **bit-identical** and
  cross-checked in `tests/test_rvl.py`. Public `compress`/`decompress` dispatch
  to NumPy when present, else fall back to pure Python (spine stays dep-free).
  On an x86 dev box: full-res masked frame compress ~68 fps (was ~20), decompress
  ~310 fps (~21×). `kinect_node` now passes the array straight to RVL (no
  per-pixel `.tolist()`). Run: `python3 -m tests.test_rvl`.
- ✅ **M2 (server side) — Live preview relay** (`central/preview_server.py`):
  node TCP `Frame` stream → RVL-decode → unproject to a metric point cloud →
  pixel-stride downsample → broadcast a binary `CPV1` message per frame to
  browser clients over a **WebSocket** (`protocol/websocket.py`, stdlib-only, no
  deps). The browser **viewer lives in the separate `crypt` repo** and consumes
  the documented contract (`docs/preview_protocol.md`). Verified end-to-end with
  **no hardware/browser** via `scripts/preview_client.py` (headless WS client):
  sim node → relay → client at ~24.6k pts/frame. **Live-validated on a real
  Jetson + Kinect** streaming to a laptop browser (~12 fps single cam).
  `frame.py` was also made Python-3.6-safe (plain class, was a `@dataclass`).
- ✅ **Live color**: depth-aligned RGB now flows through the preview path. Node
  captures `transformed_color` (BGRA in depth geometry), ships raw RGB for the
  foreground pixels only (row-major, one triple per non-zero depth pixel, new
  `FLAG_COLOR_ALIGNED` wire flag, no codec); the relay pairs each point with its
  color and adds an `rgb` block to `CPV1` (`FLAG_RGB`). `sim_node` emits a
  synthetic gradient so the color path is testable headless. Pairing verified by
  a deterministic round-trip (BGRA→RGB + row-major scatter). The `crypt` viewer
  reads the `rgb` block (`vertexColors`).
- ✅ **M1 (started) — Control plane** (`protocol/control.py`): central → node
  commands over a new `CTL1` framing, sent back down the node's existing TCP
  socket (full-duplex; a tiny idle reader thread on the node applies them — no
  frame-path impact). Path: browser WS **text** JSON → relay forwards whitelisted
  commands → node. Commands: `capture_bg`/`clear_bg`/`set_bg_margin`/
  `set_denoise` (background subtraction), `set_camera` (live mode), `set_imu`
  (orientation). Drive them headless with `scripts/send_command.py`.
  `arm/record/stop` will reuse this channel (M3). Node `run()` now `shutdown()`s
  the socket before close so the reader thread wakes cleanly.
  **Removed:** the `set_depth` near/far range-clip command — the node now streams
  the **full depth range** and culls via background subtraction + the speckle
  filter, so the depth-mask command, the node clip, and `--min-depth/--max-depth`
  are all gone (the viewer had already dropped the UI).
- ✅ **Perf/quality pass (Nano-era):** the streamed cloud was always downsampled
  at the relay (`--stride`); that now moves to the **node** (`--preview-stride`,
  carried in a new frame-header `stride` field) so RVL + color + wire all shrink
  ~stride² while the **output cloud is bit-identical** (verified). Relay
  unprojects stride-aware with **full-res intrinsics** (use `--calib`; relay
  default `--stride` is now 1). `kinect_node --profile` prints per-stage ms
  (cap/depth/color/send) to find the real bottleneck. Recording stays full-res
  (node default stride 1).
  **Measured on the Nano:** `--preview-stride 2` → **27.5 fps** (was ~12), with
  `cap 0 / RVL 22 / color 14 / send 0 ms/f` — purely CPU-bound on RVL+color, and
  ~92% of the Azure Kinect's hard **30 fps** sensor cap. So pipelining (cap &
  send are 0 → nothing to overlap except running RVL‖color on 2 cores) and
  C-RVL would each only reclaim the last ~2.5 fps and can't beat 30 fps.
  **Deferred** until they actually pay off: full-res recording and 4-cam CPU
  contention (and the Orin hits 30 at higher res regardless). Next *quality*
  levers (fps is maxed): background-plate subtraction, then AI matting (RVM) on
  the Orin.
- ✅ **Auto intrinsics** (`CCAL` handshake): each node reads its own depth
  intrinsics from the camera and sends them to central on connect, keyed by
  `sensor_id` (`frame.encode_calib`/`read_message`). No manual calib files,
  scales to N cameras. Relay `--calib` is now just an optional override; the
  node's own intrinsics win. (`calib.json` is gitignored.) This also fixed the
  "stretched cloud" bug: an out-of-date relay applied full-res `cx/cy` to a
  node-strided grid — always pull both sides together.
- ✅ **Background-plate subtraction** (`node/background.py`, `BackgroundSubtractor`):
  control commands `capture_bg` (average N frames of the empty scene → plate),
  `clear_bg`, `set_bg_margin`. Per frame, keep only pixels **closer than the
  plate − margin** → floor/walls drop at any distance, leaving the subject; far
  fewer points (big network+viewer fps win) and cleaner than the range clip. Unit
  tested (`tests/test_background.py`); forward path verified (relay→node).
  Integrated in `kinect_node`; `sim_node` just acks the commands (no real scene).
  **Perf model (measured):** node fps is **point-count-bound**, not grid-bound —
  the full-res grid scan / color warp are cheap. So **stride 1 (full resolution)
  hits 30 fps as long as the subject stays ~20–30k points**, which background
  subtraction achieves by dropping the background. `--preview-stride` is just a
  crude point-reducer (subsampling) and is unnecessary when clipping keeps the
  count in budget; point count only spikes as the subject gets closer/larger.
- ✅ **Lens-distortion correction** (`central/preview_server.compute_ray_table`):
  the node now also sends the Kinect's Brown-Conrady coeffs (`k1..k6,p1,p2`) in
  the `CCAL` handshake; the relay builds a per-sensor **ray table** via iterative
  undistortion and unprojects through it (rays × depth) instead of the pinhole
  `(u-cx)/fx`. Fixes flat surfaces bowing into "cones" on the wide-FOV depth cam.
  Zero coeffs reduce exactly to pinhole (unit-tested; round-trip recovers rays to
  ~1e-8). No `CPV1`/viewer change — the relay just emits correct XYZ.
- ✅ **Speckle filter** (`node/background.denoise_mask`): drops kept pixels with
  `< min_neighbors` valid 8-neighbours → removes the isolated ToF-noise points
  that flicker after background subtraction; the dense subject is untouched.
  Default `min_neighbors=2`, live-tunable via `set_denoise` (0 = off).
- ✅ **Live camera controls** (`node/camera_modes.py`, `set_camera` command):
  the UI picks which Kinect data to send — **depth FOV mode** (NFOV/WFOV ×
  full/binned) and **alignment direction** — live, and the stream adapts. The
  reader thread records the request; the **capture loop** does the sensor
  restart (depth mode/color res/fps) so pyk4a is touched from one thread, then
  re-reads intrinsics (depth- *or* color-camera, per alignment) and re-sends the
  `CCAL` handshake so the relay rebuilds the cloud — **no `CPV1`/viewer change**.
  Alignment: **`depth_to_color`** (default, 1 pt/*color* pixel — depth warped
  into the color grid → much more color detail / a denser cloud, the "higher-res
  color" win, at more points + some depth holes) vs `color_to_depth` (1 pt/depth
  pixel, color warped into the depth grid — fewer, cleaner points). `color_resolution`/`fps` are also
  accepted (not in the UI yet). Mode tables are pyk4a-free + unit-tested
  (`tests/test_camera.py`); `sim_node` resizes its synthetic grid + re-sends
  calib so it's testable headless; verified end-to-end (sim 640×576/98k pts →
  1280×720 color grid → 1024² WFOV, intrinsics rebuilt each switch).
- ✅ **Cross-alignment registration** (`CEXT` handshake): `color_to_depth` builds
  the cloud in the depth optical frame, `depth_to_color` in the *color* frame —
  and the Kinect's colour camera is tilted ~a few° about X + offset ~cm from
  depth, so switching alignment used to tilt/shift the cloud. The node now sends
  a **grid→depth extrinsic** (`_grid_to_depth_extrinsic` → `convert_3d_to_3d`;
  identity for color_to_depth, the factory COLOR→DEPTH transform for
  depth_to_color) alongside `CCAL`; the relay applies `P_depth = R·P + t` in
  optical space before the view flip (`unproject(extrinsic=…)`), so both
  alignments register to **one canonical depth frame**. Additive + identity-
  default (no `CPV1`/viewer change, no regression to the default path);
  unit-tested (`tests/test_extrinsic.py`).
- ✅ **Observability:** node prints a *windowed* fps (was a misleading
  cumulative average) + pts + KB/frame; relay logs `fps in | pts | KB/f |
  viewers`. Viewer gets a dual **recv vs render** fps HUD (see updates doc) so
  the bottleneck (wire vs GPU) is obvious.
- ✅ **IMU orientation** (gravity vector → cloud "up"/floor): the node reads the
  Kinect accelerometer (`_read_gravity_optical`), and sends a per-sensor gravity
  (down) unit vector alongside the `CCAL` handshake via a new `CIMU` message
  (`frame.encode_imu`/`read_message`). The relay re-expresses it in the
  cloud/view frame (`gravity_to_view`, applying the same Y/Z flip as the
  unprojector) and attaches it to **every** `CPV1` frame as a trailing optional
  block (new `FLAG_GRAVITY = 0x4`, 3×float32 after positions+rgb). Gives the
  cloud an initial orientation before extrinsic calibration; the viewer draws a
  floor grid + camera-orientation gizmo from it. The node rotates the
  accelerometer into the depth frame via the factory **ACCEL→DEPTH extrinsic**
  (`_accel_to_depth` → `convert_3d_to_3d`); without it the floor is sideways
  (the IMU has its own axes). Falls back to raw axes + a warning if a pyk4a build
  lacks it. **Live reorientation:** a `set_imu {enabled}` control command
  (off by default) makes the node re-read + re-send gravity every `IMU_EVERY`
  frames so the cloud reorients as the camera is physically turned (driven by the
  viewer's "camera orientation" toggle). To avoid lag the read **drains the IMU
  FIFO** (`_drain_accel`) and uses the freshest sample (the Kinect queues IMU at
  ~1.6 kHz; reading a couple per call consumes stale ones).
  **IMU axis convention:** the Azure Kinect IMU is rotated ~90° about depth-X, so
  left raw a level camera's gravity lands on depth +Z (forward) and the floor
  tips up onto the far wall. The node applies the built-in map `(x,y,z)->(x,z,-y)`
  (`_default_accel_to_depth`) by default → gravity back on +Y (down), verified on
  real hardware. The pyk4a factory ACCEL->DEPTH extrinsic proved unreliable
  (often not exposed) so it's **opt-in** via `--imu-extrinsic`; `--imu-axes` (e.g.
  `"x,z,-y"`, `parse_imu_axes`) overrides outright. The node logs `accel raw=… ->
  gravity(optical)=…` for diagnosis.
  `sim_node` emits a known-good vector (and wobbles it while streaming) so the
  path is testable headless; unit-tested (`tests/test_imu.py`) and verified
  end-to-end (sim→relay→browser).

- ✅ **Multi-core node pipeline** (hardware-validated on the Orin: subject at
  ~1.5 m in depth_to_color = a sensor-limited **30 fps**, was 25 serial; under
  full-room saturation ~0.3 s of pipeline latency is *inherent* — ≥4 frames in
  flight to keep the workers fed — and only affects the setup view, not the
  subtracted subject path): the node's serial loop (cap+mask/RVL+color+send on ONE core = stage *sum* per
  frame; measured 40 ms in depth_to_color close-up → 25 fps with 5 Orin cores
  idle) is now capture thread → worker **PROCESS** pool (`_process_frame`, pure
  NumPy) → ordered sender. **Processes, not threads — hard-won:** the stage is
  ~40 short NumPy calls, and on threads CPython's GIL convoys them (measured on
  the Orin: all cores idle, clocks maxed, stage wall time 1143 ms for 443 pts —
  ~30×). The pool is forked before the camera/socket/threads exist; children
  only run `_process_frame`. pyk4a stays single-threaded on the capture thread;
  the sender emits in submission order so the wire is unchanged; socket death
  still raises out of `run()` (systemd restarts). **Freshness beats
  completeness:** the queue is shallow (workers+1) and when it's full the
  capture thread **parks in a sleep** (`| sat N%` in the stats line = % of the
  window spent parked) — it must NOT spin through SDK calls (that GIL churn is
  what starved the workers), and a deep queue turned overload into ~700 ms of
  view lag (hand-wave played back after the wave); the Kinect's internal queue
  discards stale frames while parked so the next capture is fresh. Live preview
  must show *now*; recording (M3) is a separate node-local path.
  `--workers` (default 2; raise toward 4 for full-room). Default `align` flipped
  to **color_to_depth** (native depth grid holds a sensor-limited 30 fps; the
  viewer default was flipped to match — it resync()s align on every connect).
  Verified headless: stubbed-pyk4a integration test (order, payload integrity,
  freshness-under-overload via parking, dead-central raise);
  `tests/test_camera.py` updated for the new default.
- ✅ **Rig extrinsic calibration wiring (M5 first half, 2026-07-02)** — the
  marker-ball math core is now wired end-to-end (design + verification detail:
  `docs/rig_calibration.md`):
  **collect+solve** — `scripts/calibrate_rig.py` (headless WS client: gates
  frames implausible for the ball via `central/calibration.BallTracker`
  count/fit-rms gates, fits centers, `solve_rig`, writes `rig_calib.json`);
  **apply at the relay** — `preview_server --rig-calib` (default
  `rig_calib.json`, absent = exact no-op): `P_world = R_i·P + t_i` per sensor
  after unprojection (gravity rotated too) → ONE canonical world frame on the
  wire, no `CPV1` change; the file is mtime-watched (re-runs re-register
  live) + a `reload_rig_calib` command;
  **poses/status to the viewer** — JSON TEXT messages on the preview WS
  (`rig_poses` on connect + every (re)load/clear; `calib_status` progress),
  spec in `docs/preview_protocol.md`;
  **viewer-driven sessions** — relay-handled `calibrate_fine {seconds,
  ball_radius}` / `calibrate_rough {seconds}` commands (the crypt viewer's
  Fine/Rough Align buttons + ball-radius field drive them; also
  `scripts/send_command.py calibrate-fine/-rough/reload-rig-calib/
  clear-rig-calib`). Sessions collect off the RAW pre-transform clouds so
  re-runs are correct; `clear_rig_calib` (the viewer's **Reset** button)
  cancels a running session, deletes the file and returns to raw frames.
  Rough needs no IMU toggle (uses the connect-time gravity); per-tier operator
  steps ("walk a slow L…", "wave the ball slowly…") are in the doc + panel.
  **Tier-1 rough solve** — `solve_rough` = per-sensor IMU leveling
  (`level_rotation`) + yaw-only Kabsch (`solve_yaw_translation`) on
  body-centroid tracks (yaw-only on purpose: the centroid's toward-camera bias
  would corrupt a full 3D solve; roll/pitch come from the IMU); world = the
  ref sensor's leveled frame (floor flat by construction).
  **Per-sensor floor leveling ("floor" tier, 2026-07-03)** — one rigid
  correction can only flatten ONE plane, so `calibrate_floor {seconds:3}`
  fits each camera's floor in its OWN raw cloud (`fit_floor`: lowest dense
  band along the IMU up hint + LS refine; floor must be in view → background
  subtraction off) and composes per-sensor corrections onto the current rig
  (`solve_floor_level`: normal→+Y about the floor centroid, common height) —
  all floors flat + coplanar on the wire; yaw/XY untouched. For
  uncalibrated/rough rigs only (a fine wand calib is already mm-coplanar; the
  viewer's Detect Floor routes accordingly and chains its local grid snap).
  **Relay write-lock fix (2026-07-03)** — WS frames to a client are now
  serialised per connection: concurrent writers (per-sensor node threads +
  status broadcasts) interleaved bytes mid-frame on large sends and browsers
  dropped the socket ("Invalid frame header"). *(Superseded 2026-07-04: the
  per-client `ClientSender` thread is now the socket's only writer — same
  guarantee, no lock; see the multi-viewer entry below.)*
  **Unknown-command NACK (2026-07-03)** — the relay replies
  `{"type":"cmd_error", cmd}` to browser commands it doesn't recognise
  instead of dropping them silently; the viewer turns that (plus a 4 s
  no-reply watchdog) into "update the relay" guidance — version skew between
  the repos kept masquerading as broken buttons.
  **Headless proof** — `sim_node --ball 0.05 --pose "yaw,x,y,z[,pitch]"
  [--floor Y]` ray-renders a shared wall-clock-driven sphere (+ optionally
  the world floor plane) from a known pose (pose-true IMU vector);
  two posed sim nodes → calibrate_rig recovered 50°/1.2 m ground truth to
  **0.16°/3 mm**, wire clouds register, viewer verified in headless Chromium
  (incl. two differently-pitched cameras → one Detect Floor click → both
  floors flat on the grid to 0.006°). `tests/test_rig.py` covers
  trackers/gates, rough solve, floor fit/level, JSON round-trip and the apply
  step. Remaining: the real-hardware wand pass, then TSDF fusion.
  **Fine-pass ball segmentation + live feedback (2026-07-03)** — the wand pass
  was near-unusable: it fit a sphere to the WHOLE per-sensor foreground, so it
  only detected the ball when the ball was the *only* foreground (impossible on
  an inward rig where your body is always in some camera → every such frame
  silently dropped), and the operator waved the ball blind. Now
  `calibration.segment_ball` pulls the **largest spherical cluster** out of the
  (background-subtracted) foreground via voxel connected-components + per-cluster
  `fit_sphere` (best radius match wins; body/arm clusters fail extent/rms gates)
  — **body-in-frame is fine**. The relay streams the detected centre per sensor
  (in the wire-cloud frame) inside `calib_status.balls`; the viewer draws a LOCK
  sphere at the configured radius + a per-camera `●rms/○` indicator, and now
  **requires background subtraction** before Fine Align. Ball spec: ~15–20 cm
  matte (not black/glossy), measure the radius. Unit-tested
  (`test_calibration.segment_ball`, `test_rig` gates) + headless E2E (two posed
  sim balls → `balls` feedback flows, solve lands 2.4 mm). Optional future
  upgrade: retroreflective ball in active IR for even more robust detection.
  **Robustness pass (2026-07-03, first real-hardware run):** first wand test
  showed two failures — the lock sometimes jumped onto legs/non-spherical
  features, and the completed fine solve snapped to *completely wrong* poses,
  worse than rough. Fixes: (a) `segment_ball` gained a **sphericity gate**
  (PCA `√(λ2/λ1) ≥ 0.5` rejects elongated leg/arm clusters) + an extent lower
  bound; (b) `solve_rig` now solves via **`solve_rigid_ransac`** (3-point
  minimal fits, 3 cm consensus, refit on inliers) so the inevitable few
  ball/leg mis-locks can't corrupt the rigid solve — recovers pose to mm with
  20–30 % outliers (unit-tested); (c) the fine world is post-leveled by the
  **reference sensor's IMU** so a fine pass refines the rough frame instead of
  jumping to a tilted one. A bigger ball (~15–20 cm) further sharpens
  discrimination (wrong-curvature body bumps blow the known-radius fit).
  **Stop-and-go fine pass (2026-07-03, now the default fine mode):** continuous
  waving pairs each camera's ball centre by nearest timestamp, but on an
  UNSYNCED / slow rig (old Nano ~10–15 fps) the cameras grab a MOVING ball at
  different instants → paired points are the ball at different places (error =
  speed × time-skew, tens of mm, in every pair — RANSAC can't remove a common
  bias). `StationaryBallSampler` fixes it: a camera is "still" when its detection
  SPEED across the ~0.8 s window is under `max_still_speed` (default 1.5 cm/s — a
  velocity gate measured from the window's two half-means, so ToF jitter is
  averaged out and a slow transition between spots is NOT mistaken for a hold;
  first-hardware tuning), and when ≥2 cameras are still at
  once AND the ball has moved to a new spot, commit ONE window-averaged sample
  per still camera under a shared capture id (exact correspondence key — no
  max_dt guess); auto-finish at a target capture count. A stationary ball is at
  the same place regardless of per-camera timing, so the skew error is gone, and
  window-averaging also cuts ToF noise (unit-tested: two cameras sampled at
  different instants recover 0.02°/0.4 mm). Feedback carries per-sensor `still`
  + a `captures` count at ~4 Hz (was 1 Hz, felt laggy); the viewer greens the
  LOCK sphere when a camera settles. `mode:"continuous"` (old `BallTracker`)
  stays for hardware-synced rigs; the viewer button sends `mode:"stationary"`.
- ✅ **Skeleton pipeline wiring (2026-07-03, design: `docs/skeleton_pose.md`)**
  — 2D pose keypoints from the nodes, lifted to metric 3D at the relay.
  New `CPOS` node→central message (`frame.encode_pose`, 3.6-safe): COCO-17
  `(joint_id, u, v, z_m, conf)` in full-res grid coords (color is
  depth-aligned in both modes, so the node ships 2D+depth and central owns
  the 3D lift via its ray tables). Relay `_on_pose`: unproject → feed any
  active skeleton-capable calibration session (raw) → apply rig → broadcast
  `{"type":"skeleton"}` TEXT to viewers (same frame as that sensor's cloud).
  **Rough Align auto-upgrades**: `calibrate_rough` collects a `JointTracker`
  alongside the centroid track and prefers `solve_skeleton` (per-joint
  `pair_tracks` stacked into a full 3D Kabsch, tier `"skeleton"`, ~2–5 cm —
  a named joint is the same physical point from every view, unlike the
  centroid) with the centroid+IMU solve as fallback. Decisions: RTMPose
  (Apache-2.0) via TensorRT ON THE NODE (GPU idle, capture is CPU-bound;
  inference decoupled latest-frame-only so the cloud stream can never wait
  on it); DeepStream rejected (video-pipeline framework, wrong shape);
  central-side inference = supported fallback for the weak Nano — **BUILT
  (2026-07-03): `preview_server --pose-model models/movenet.onnx`** lazily
  runs the SAME `node/pose.PoseWorker` at the relay per sensor, fed by the
  color grid it already rebuilds (`aligned_color_grid`), keypoints injected
  into `_on_pose` like a node's CPOS; node-side CPOS in the last 2 s
  suppresses that sensor's central worker (Orin keeps TensorRT on-node, only
  the Nano falls back; a dead node worker auto-fails-over after 2 s). Needs
  plain `pip install onnxruntime` on the laptop (x86 CPU ~25 ms). Tested
  (unit + relay/sim E2E).
  Headless: `sim_node --skeleton` (synthetic wall-clock person projected
  through `--pose`), `tests/test_pose.py`; two posed sims → Rough Align
  auto-upgraded and recovered ground truth to 0.24°/6 mm; viewer markers +
  toggle verified in Chromium. **Node inference worker BUILT**
  (`node/pose.py` + `kinect_node --pose-model models/movenet.onnx`): v1
  model = MoveNet single-pose ONNX via plain-pip onnxruntime (zero-friction
  on JetPack; RTMPose/TensorRT is the upgrade path) — `MoveNetEstimator`
  (variant-tolerant NHWC/NCHW + int32/uint8/float, pure-NumPy letterbox) +
  `PoseWorker` (own thread, latest-frame-only, capped intra-op threads,
  emits via the ordered sender queue → cloud stream can never wait on
  inference; per-joint One-Euro smoothing (default on) + a torso-confidence
  person gate `--pose-gate` that suppresses whole frames so skeletons stop
  appearing on furniture — first-hardware findings 2026-07-03, tuning ladder
  for lag in the doc). **Stability pass (2026-07-03 third):**
  confidence-WEIGHTED smoothing (a low-conf sample — blurred hands — only
  nudges the filtered joint; conf ≥ 0.6 passes through untouched) + person-
  gate HYSTERESIS (acquire after 3 consecutive passing frames, release after
  5 fails) so isolated chair flukes never emit and mid-track confidence dips
  don't drop the skeleton; both unit-tested. Jitter/confidence also depends
  on the model: prefer MoveNet **Thunder** (256 px) over Lightning on the
  Orin — TRT headroom is huge; swap = re-download + clear models/trt_cache.
  Estimator verified against a dummy ONNX with MoveNet's exact interface,
  then **validated on the Orin** (2026-07-03): bench (`python3 -m node.pose
  <model> [--trt|--cpu]`) measured CPU 25 ms / CUDA 7.3 ms / TensorRT 2.9 ms
  (254 fps sustained) — but in-node inference ran 60-70 ms until the worker
  moved into its OWN PROCESS (`PoseProcess`, forked before the camera; the
  pose thread was convoying on the capture loop's GIL, the same lesson as
  the frame worker pool). Production config: `--pose-model models/movenet.onnx
  --pose-trt` (engine cached in models/trt_cache) → skeletons at camera rate.
  **Full-res streaming is the default EVERYWHERE** (2026-07-03, user call —
  max res on every node, the old Nano included): point-count-bound throughput
  means stride 1 holds 30 fps with background subtraction; `--preview-stride 2`
  survives only as a per-device EXTRA_ARGS opt-in if a specific device's CPU
  can't keep up. `models/` is gitignored (survives the auto-update hard reset).
  **Device-class profiles in the repo (2026-07-03):** the service now launches
  via `deploy/run-node.sh`, which auto-detects the device class from
  `/etc/nv_tegra_release` (L4T R34+ → `orin`, R32/R28 → `nano`, unknown →
  `default`; force with `NODE_PROFILE=` in `/etc/default/kinect-node`) and
  sources `deploy/profiles/<class>.env` — orin = `--pose-model
  models/movenet.onnx --pose-trt` (safe pre-setup: missing model/runtime →
  "pose disabled", streaming unaffected), nano = full res, no pose. The
  env file's `EXTRA_ARGS` is appended AFTER the profile (argparse last-wins)
  so it's per-device overrides only. Profiles being in-repo means new default
  flags roll out via the auto-update; the unit change itself needs ONE
  `sudo deploy/install-node-service.sh` re-run per device. ⏳ remaining:
  hands→particle attractors in the viewer; relay-side skeleton fusion across
  sensors.
- ✅ **CPV1 grid block → viewer textured mesh (2026-07-03)**: the relay now
  attaches each point's **depth-grid position** to every `CPV1` frame as an
  additive trailing block (new `FLAG_GRID = 0x8`, after gravity: `u16 grid_w`,
  `u16 grid_h`, then `count × u32` row-major linear index into the strided
  sub-grid, ascending, paired 1:1 with positions). That's the connectivity the
  flat point list otherwise loses — the `crypt` viewer re-meshes neighbouring
  grid pixels into triangles and renders the subject as a **textured mesh**
  (its new `MeshCloud` + panel `render` selector; much better facial detail),
  swappable live with the classic point render. `unproject` returns
  `(xyz, rgb, grid)` now (`np.flatnonzero` of the valid mask — zero extra
  compute). **`--max-points` is enforced grid-aware** (same day, first
  hardware test): the old point-wise linspace trim punched a periodic hole
  every N points — gap stripes in the point render, a hole lattice in the
  mesh, and an EMPTY mesh in depth_to_color (921k candidates ≫ cap → no
  surviving adjacency); `unproject(max_points=…)` now coarsens the sampling
  stride (denser axis first) until the count fits, so a capped frame is a
  coarser but fully-connected grid. **`--max-points` defaults to 0 =
  UNCAPPED** (user call: full resolution for points and mesh; meshing targets
  the background-subtracted subject, and even a full-environment frame holds
  interactive fps on a LAN — the viewer preallocates 1M points/sensor, a full
  1280×720 depth_to_color frame). Grid block on by default (+4 B/pt ≈ +27%
  frame size); `preview_server --no-grid` drops it. Spec in
  `docs/preview_protocol.md`; verified headless (`scripts/preview_client`
  asserts ascending in-range indices; sim → relay → viewer mesh rendered in
  Chromium, incl. the old-relay fallback to points and a forced
  `--max-points 5000` run meshing hole-free). `tests/test_grid.py`.
- ✅ **Multi-viewer fix — per-client sender threads (2026-07-04)**: a second
  browser connecting to the relay (e.g. the VR PC viewing the laptop's
  stream) dropped EVERY viewer to ~2 fps. Cause: the relay broadcast inline
  in each node's handler thread with a blocking `sendall` per client — one
  viewer whose link couldn't drain the stream (~90 Mbps for a subtracted
  ~25k-pt cloud at 30 fps, far more full-room; hopeless on Wi-Fi) filled its
  TCP buffer, blocked the node thread, and backpressured the Jetson, so all
  viewers ran at the slowest client's rate. Now each viewer socket gets its
  own `ClientSender` thread fed via a **latest-frame mailbox**: node threads
  hand frames off without ever touching viewer sockets; binary cloud frames
  overwrite a per-sensor slot (a slow viewer skips stale clouds and renders
  the freshest its link carries — freshness beats completeness, the node
  pipeline's rule), TEXT/JSON messages are ordered and never dropped, and
  the sender being the sole writer replaces the 2026-07-03 per-client write
  lock. `_ws_reader` also treats a hard connection reset as a disconnect
  (no traceback spam). No wire/protocol change. Unit-tested
  (`tests/test_sender.py`: latest-wins, text lossless, producer never
  blocks on a wedged client, dead socket drops cleanly) + E2E (sim → relay
  → one never-reading viewer + one fast viewer: fast viewer stays at the
  full ingest rate; the pre-fix relay starved it to 0). Per-viewer fps is
  now bounded only by that viewer's own link/GPU — a remote PC still needs
  the bandwidth for 30 fps (Ethernet both hops, or keep background
  subtraction on; JPEG/NVENC color transport remains the deferred lever for
  many viewers).
- ✅ **Relay ingest freshness + per-sensor CPU parallelism (2026-07-05)** —
  two fixes for a **relay that can't keep up** (an older/slower central
  machine on a heavy full-room stream), found debugging an X99 PC running
  ~10 fps full-room / **playing the cloud in slow motion** while a laptop did
  22 fps. Root cause was NOT the network (proven clean 746 Mbps by iperf) or
  the PC's many cores (14% busy) — it was the relay's **single-threaded
  per-sensor** read loop being CPU-bound on the older core, and TCP delivering
  the resulting backlog oldest-first. Fixes, both in `_serve_node`:
  **(1) drop-stale on ingest** — after reading a frame the reader
  non-blocking-drains any frames already queued on the socket
  (`select`, 0 timeout), keeps only the FRESHEST and drops the stale ones (the
  node pipeline's "freshness beats completeness" rule applied to the relay's
  READ side; control messages in the backlog still applied in order via
  `_handle_node_control`). A slow relay now renders fewer fps but **always
  live** — no more growing delay / "slow motion"/catch-up. Disabled while
  recording/calibrating (they must consume every frame). Stats line adds
  `N stale skipped`.
  **(2) `--workers`** (default **`auto`** = `cpu_count-2`, capped at 8; an
  integer forces it, `1` = single-threaded) — fans the heavy STATELESS stage
  (unproject + build_message) across ONE shared thread pool (relay-wide, not
  per node connection, so N sensors don't oversubscribe to N*workers threads)
  so ONE sensor's stream can use multiple cores; the stateful/ordered part
  (decode + temporal/spatial denoise + pose submit + ray-table build) stays on
  the reader thread. Scaling is sub-linear (GIL on the Python glue + the
  un-parallelizable decode/denoise floor), so `auto` caps at 8.
  numpy releases the GIL during the big array ops so threads scale; frames
  retire IN ORDER with a bounded window, so latency stays fixed and the
  recording tee stays ordered (parallel output is byte-identical to
  sequential — `tests/test_relay_workers.py`). Use `--workers 4` on a
  CPU-bound central box for full-room fps. Note: **background subtraction
  already keeps the subject light enough for 30 fps on a slow relay** — these
  fixes are for the full-room setup view. Unit-tested
  (`tests/test_ingest_freshness.py`: freshest-survives + recording-keeps-all;
  `tests/test_relay_workers.py`: parallel == sequential bytes) + E2E
  (sim→relay→client with `--workers 4`).
- ✅ **Relay zero-waste frame assembly + measured full-cloud budget
  (2026-07-05)** — a hot-path microbench (full un-subtracted frame, per stage,
  1 core, on a 2.8 GHz Xeon) to see what REALLY caps full-cloud fps. Two safe,
  byte-identical relay-only copies removed: `build_message` now `b"".join`s the
  blocks instead of a `payload += chunk` chain (each `+=` reallocated+copied the
  whole growing buffer — O(n²) traffic; 2.6→1.2 ms NFOV, 9.7→6.2 ms 720p), and
  `websocket.encode_frame` concatenates the multi-MB payload ONCE instead of
  `out += payload; bytes(out)` copying it twice (**19→3 ms at 720p**, 1→0.5 ms
  NFOV). Both come straight off the per-sensor reader-thread budget; all
  relay/grid/recording tests stay green (output unchanged). **The measured
  headline: full cloud has TWO independent walls, and for the "30 fps full cloud,
  no bg" goal the WIRE binds first, not relay CPU.**
  *Wire:* CPV1 is 19 B/pt uncompressed (12 xyz f32 + 3 rgb + 4 grid u32) →
  **~1.6 Gbit/s per sensor** (NFOV 640×576 full, ~358 k pts) to **~4 Gbit/s**
  (720p depth_to_color, ~890 k pts). One full-cloud sensor already exceeds a
  gigabit LAN; 3 cams full-cloud ≈ 5 Gbit/s — unreachable without 10 GbE or wire
  compression. This is exactly why background subtraction (~25 k pts ≈ 114
  Mbit/s) "just works" and full cloud doesn't — it's physics on the wire, no
  relay-CPU fix reaches it. *Relay CPU:* the per-sensor reader-thread SERIAL
  chain (rvl.decompress + temporal denoise + color-grid — all must stay ordered)
  is ~38 ms NFOV / ~120 ms 720p on that Xeon → a **single sensor caps ~26 fps
  NFOV even with infinite --workers**; `rvl.decompress` alone is ~24 ms (the
  single biggest stage, and NOT pooled — deliberately left untouched, it's
  bit-identical-tested). Each sensor has its own reader thread so N cams
  parallelize across cores; the shared pool only speeds the stateless
  unproject+build tail. Practical levers documented for the operator: **run the
  realistic (background-subtracted) mode** — it fits gigabit and scales to 3
  cams trivially; for a full-cloud test add `--no-temporal-denoise` (drops ~7 ms
  NFOV / ~22 ms 720p off the serial chain) and `--workers` on a many-core box.
  ⏳ The real full-cloud unlock (if ever needed for VR environment capture) is a
  **CPV2 wire format**: int16-quantized positions (12→6 B) + a valid-mask
  BITMAP instead of the u32 grid indices (4 B/pt → ~0.13 B/pt on a full frame)
  ≈ 9 B/pt (−52 %) — a coordinated crypt-viewer change, not yet built.
- ✅ **Scene recording + in-scene playback (2026-07-04)** — the "hit Record on
  a running scene" milestone (`central/recording.py` + relay wiring; spec in
  `docs/preview_protocol.md` "Scene recording"). `record_start`/`record_stop`
  (viewer Record button or `send_command record-start/-stop`) tee every
  outgoing `CPV1` message — the already-registered, background-subtracted
  bytes every viewer renders — into a `CPR1` take (`recordings/<id>.cpr` +
  JSON sidecar meta). **Seamless by construction:** the tee is an O(1)
  non-blocking enqueue on the node threads with a dedicated writer thread; a
  too-slow disk drops-and-counts frames (reported in meta/status) instead of
  ever stalling the live path. `record_status` (~1 Hz) + a `recordings`
  index (on connect + every stop/delete) keep every viewer's panel in sync;
  `list_recordings`/`delete_recording` round out the command set. The ws
  port now also answers **plain HTTP** (`GET /recordings` index +
  `GET /recordings/<id>` take download, CORS-open — the delivery path for
  the future "pick a record and experience it" web app; the browser port
  handler was split so non-upgrade requests route to HTTP). The `crypt`
  viewer plays takes back INTO the live scene through its same renderer
  cores (recorded format == wire format — the North Star contract).
  Unit-tested (`tests/test_recording.py`: round-trip, non-blocking tee +
  drop-over-cap, truncation, id safety, commands, HTTP) + E2E (sim → relay →
  record via send_command → HTTP download byte-identical + 45 CPV1 frames;
  viewer record→list→play→loop verified in headless Chromium). NB this is
  the wire-stream recorder (preview-resolution); M3's node-local
  full-fidelity record/download is still separate/future.
- ✅ **XR pose passthrough (2026-07-04)** — `{"cmd":"xr_pose", head, ctl,
  rect}` from a presenting viewer is rebroadcast by the relay as
  `{"type":"xr_pose", sid, …}` TEXT to every OTHER viewer (sender excluded;
  `sid` = stable per-connection id, freed on drop), so any desktop viewer
  draws a live headset/controller/play-area gizmo of whoever is in VR (the
  crypt panel's "vr space" section — its sliders reposition the VR tracking
  space on the viewer's camera rig, all viewer-side). Relay is a stateless
  wire here; ~10 Hz tiny JSON on the ordered TEXT path. Spec entry in
  `docs/crypt_viewer_updates.md`; unit-tested (`tests/test_xr_pose.py`).
- ✅ **Temporal depth denoise (2026-07-05, merged to main from
  `experimental/temporal-depth-denoise`; ON BY DEFAULT since 2026-07-05)** —
  `central/temporal_denoise.py`, a per-pixel One-Euro low-pass filter over
  the raw depth grid, applied at the relay right after RVL decode and
  BEFORE unprojection/color-alignment/pose-lift — kills the ToF's per-pixel
  depth jitter (reads as "every point is vibrating" in the viewer, worst in
  VR where the eye is close to the surface) while staying responsive to
  real motion (same adaptive filter already smoothing skeleton joints,
  `node/pose.py`'s `OneEuro`, vectorized over the whole depth array here).
  Correct rather than a smear because a depth pixel (u,v) is the SAME
  physical ray every frame — filtering keyed by pixel, before the valid-set
  is flattened into a point list, is filtering one signal over time (the
  flat XYZ list can't do this: index i is a different physical point every
  frame as the mask shifts). **Relay-only, ON BY DEFAULT** (per-pixel over
  time = a couple of vectorized passes, negligible fps cost, and it only
  helps): `preview_server` runs it automatically; `--no-temporal-denoise`
  turns it off, `--denoise-min-cutoff`/`--denoise-beta` tune it (the
  `--temporal-denoise` flag is now a no-op kept for compat). No node/protocol
  change, so it's a relay-only toggle while the Jetsons keep running
  untouched. The critical invariant — the filtered
  depth's zero/non-zero mask must stay BYTE-IDENTICAL to the raw depth's,
  or `aligned_color_grid`'s RGB pairing silently breaks — is enforced
  explicitly and covered by a dedicated test. Unit-tested
  (`tests/test_temporal_denoise.py`: measured noise reduction on a
  synthetic static+jitter signal, step-response lag bound, mask
  preservation across random frames, fresh-seed vs stale-gap reseeding,
  short-dropout continuity, per-sensor independence, shape-change reset) +
  E2E (real `preview_server` + real `sim_node` over real sockets, 40
  frames: identical point counts/fps with the flag on vs off, zero
  RGB/depth count mismatches). Defaults (`min_cutoff=1.0, beta=0.01`) are a
  first estimate — no real noisy-Kinect data was available to tune
  against here, so expect to retune by eye once running against a real,
  noisy Kinect (⏳ open follow-up).
- ✅ **Spatial (within-frame) depth denoise (2026-07-05)** — the COMPANION to
  the temporal filter, also OPT-IN, off by default: `central/spatial_denoise.py`
  (`SpatialDepthFilter`), an **edge-preserving bilateral** filter across
  NEIGHBOURING depth pixels within one frame, applied at the relay right after
  the temporal filter and BEFORE unprojection/color-alignment/pose-lift. The
  two filters attack ToF noise on different axes: temporal smooths one pixel
  *over time* (can't touch a single frame's spatial roughness → a still subject
  still has a pebbled surface); spatial smooths *across pixels* within a frame
  → flattens that grain immediately, even on the first frame / a subject that
  never holds still. They compose (run either/both/neither). **Bilateral, not a
  blur**: each neighbour is weighted by BOTH spatial closeness (Gaussian on
  pixel distance) AND depth similarity (Gaussian on depth difference,
  `sigma_depth` mm — the "range" term). The range term makes it edge-preserving
  — same-surface neighbours differ by ToF jitter (a few mm) so they average and
  the grain smooths out, but a silhouette (subject 1.2 m vs wall 2.5 m) is a
  jump of hundreds of mm so those across-edge neighbours get ~zero weight and
  are excluded → the edge stays crisp and no phantom mid-depth bridge points
  appear (same principle as the viewer's `MeshCloud` `maxEdge` cut, but done at
  the relay over the depth grid so the POINT render benefits too, and every
  viewer/recording gets it for free). Doing it in DEPTH-GRID space (before the
  flat point list) is what makes "neighbour" a cheap fixed array shift — no
  KD-tree. **Stateless** (no per-sensor memory, unlike the temporal One-Euro →
  a camera-mode switch just works). Preserves the same critical invariant: the
  filtered depth's zero/non-zero mask stays BYTE-IDENTICAL to the raw (invalid
  neighbours are missing measurements, excluded from every average; a valid
  centre always keeps its self-weight so it can't collapse to 0). **Relay-only,
  opt-in** (`preview_server --spatial-denoise` [+ `--spatial-radius` 1=3x3 /
  2=5x5, `--spatial-sigma-depth` mm]) — one-flag toggle, no node/protocol
  change. Unit-tested (`tests/test_spatial_denoise.py`: measured noise
  reduction on a flat surface, edge preservation / no phantom bridge, hole-
  neighbour exclusion, mask preservation across random frames, statelessness,
  array-input from the temporal filter) + E2E (real `preview_server` + real
  `sim_node`: point count identical on vs off, colour pairing intact).
  **Perf** (it runs inline in the node handler thread, so its ms come straight
  off relay fps): cost scales with GRID PIXELS SCANNED, not point count. The
  range weight is computed in-place (reused scratch buffers, no per-offset
  allocation) and the frame is CROPPED to the valid-pixel bounding box first,
  so a background-subtracted subject (the intended target) is ~3 ms at
  1280x720 r=1 — essentially free. A FULL un-subtracted environment frame is
  the costly case (whole grid valid): ~13 ms at 640x576, ~45 ms at 1280x720
  (r=2 ≈ 2.3x). So if full-room fps drops: turn on background subtraction
  (biggest win, and the intended mode), keep r=1, or leave it off for the
  setup/environment view. Defaults (`radius=1, sigma_depth=30 mm`) are a first
  estimate — retune by eye against a real noisy Kinect (⏳ open follow-up, same
  as temporal).
- ✅ **LAN auto-discovery** (`protocol/discovery.py`): the node finds the central
  relay by a **rig id** instead of a hardcoded IP, so the central laptop getting a
  new DHCP lease needs no reconfig. UDP broadcast (port 9001): node broadcasts
  `CRYPTDISC1 Q <rig_id>`, the relay's responder thread replies
  `CRYPTDISC1 R <rig_id> <node_tcp_port>` and the node learns central's IP from
  the reply's source address, then connects TCP as before. Enabled with
  `--host auto` (node, the systemd default) + on by default in the relay
  (`--rig-id`/`--no-discovery` to tune). Stdlib-only + 3.6-safe; `sim_node` also
  supports `--host auto`; unit-tested (`tests/test_discovery.py`, loopback +
  broadcast round-trip). Falls back to a fixed host/mDNS/DHCP-reservation where
  Wi-Fi blocks broadcast (AP isolation) — see `docs/jetson_setup.md` §9.

## The big technical decisions (and WHY) — from a deep-research pass

- **Geometry-based, NOT Gaussian splatting.** We have real metric depth from 4
  Kinects. 3D/4D Gaussian splatting *throws depth away* and re-derives geometry
  via per-scene training (COLMAP + hours of optimization) at video-grade
  bandwidth; web playback of *dynamic* splats is bleeding-edge (one vendor,
  Gracia). Lean into the depth. Revisit 4DGS only as a far-future fidelity bet.
- **Representation = TSDF fusion → watertight mesh per frame ("approach B").**
  Fusing 4 views into one signed-distance field *dissolves seams* by
  construction (this is what Depthkit Studio does internally). Trade-off:
  variable topology per frame → stream as per-frame meshes (not VAT).
  - **Upgrade "approach C":** because the subject is always a HUMAN, fit/track a
    parametric body template (**SMPL-X**) + displacement to get *consistent
    topology* → VAT-able, temporally coherent, tiny. The AI path; more R&D.
- **Keep the per-sensor depth-grid structure.** A depth map has free
  connectivity (connect pixel neighbours, cut on depth discontinuity) and, if
  the grid is constant, fixed topology → VAT (one draw call). The KEY past
  mistake: the old Brekel *point-cloud* export discarded the grid (flat,
  variable-count xyz list). Capture **raw per-sensor depth** (Azure Kinect SDK /
  Open3D), do NOT use fusing exporters (LiveScan3D/Depthkit Studio/Brekel/EF EVE
  all fuse and lose the grid).
- **Cleanup = per-view AI matting** (Robust Video Matting, or BackgroundMattingV2
  with a background plate since the rig is static) beats sparse skeleton
  clipping for clean hair/finger edges. Run it ON the node to cut bandwidth.
  (RVM is GPL-3.0 — licensing flag.)
- **Depth transport = RVL** (Microsoft Research lossless depth codec, designed
  for many Kinects over LAN). **Color = NVENC** H.26x on the node.
- **Web delivery = glTF + `meshopt`, NOT Draco.** Draco is static-geometry-only
  (can't compress morph/animation) and reorders vertices (breaks VAT/morph).
  meshopt preserves order, compresses animation, fast Wasm decode. Vertex color
  now; texture-as-video (UVOL/Arcturus style) later for photoreal. Mesh-sequence
  web playback is production-ready (Arcturus HoloSuite, UVOL); your VAT renderer
  is the DIY version.
- **Distributed capture.** One Kinect per edge node (4 Kinects on one PC
  saturates USB3 controllers). HW frame-sync via the 3.5 mm daisy-chain between
  cameras; central machine sends the trigger (arm/record/stop) and does
  alignment + fusion. Each node also does the AI matting (distributes the ML,
  streams only the masked foreground).
- **Node hardware.** Azure Kinect SDK is archived + x86-first; **Body Tracking
  does NOT run on ARM/Jetson**; new Jetson OS (Orin/JetPack 5-6) fights the old
  depth-engine binary. **Lowest-risk node = x86 mini-PC + small NVIDIA GPU.**
  The Jetson Nano works (proven JetPack 4 / Ubuntu 18.04 combo) and is great for
  free validation but too weak for production matting. (If Jetson: Orin NX, not
  Orin Nano — Nano has no NVENC. Azure Kinect itself is discontinued; Orbbec
  Femto Bolt is the successor.)
  **Orin Nano migration in progress** (the user bought the Nano despite the NVENC
  caveat — fine, its color path is codec-less today so NVENC isn't on the critical
  path; software/FFmpeg encode later if needed). Key facts: the Orin Nano *cannot*
  run JetPack 4/Ubuntu 18.04 (different SoC). Flash **JetPack 6.2 / Ubuntu 22.04 /
  Python 3.10** — the latest well-supported on the Orin Nano, and k4a is
  community-confirmed on 22.04; 5.1.x/20.04 is now *harder* to flash (NVIDIA
  defaults to JP6) with no real benefit, keep it only as a fallback, and avoid
  JP7 (too new for the 18.04-era depth engine). The SD card can't be physically
  moved from the Nano (reflash a new card; 128 GB is plenty since the node is a
  bridge that offloads+clears — long-term recording buffer belongs on an NVMe SSD
  via the devkit's M.2 slot, not the SD). The archived Azure Kinect SDK has no
  native 20.04/22.04 ARM64 packages, so install the **18.04 arm64** ones (+
  `libsoundio1`) + the `libdepthengine.so.2.0` binary; the depth engine still
  needs a GL context. The Orbbec K4A wrapper does **not** support the original
  Kinect DK (Femto only). Node code is already 3.6-safe so it runs unchanged on
  3.10. **Migration DONE + verified on hardware** (JetPack 6.2, one Orin Nano
  streaming live over Ethernet as a boot service): the clean per-node runbook is
  **`docs/jetson_orin_node_setup.md`**; **`docs/jetson_orin_migration.md`** is the
  why/gotchas companion. Key hard-won facts baked in: `libsoundio1` was dropped
  from 22.04 (pull the 20.04 arm64 .deb first — the one make-or-break step); the
  1.4.2 `libk4a` deb bundles the depth engine; udev rules are required (missing →
  "libusb unavailable"); JetPack 6 defaults to Wayland so force Xorg+autologin and
  give the service `DISPLAY=:0`+`XAUTHORITY=/run/user/1000/gdm/Xauthority` for the
  depth-engine GL context (no error 204); the depth cam (`097c`) cold-boot
  enumeration self-heals on a normal reboot (USB bus cycles), else power-cycle the
  Kinect's 5 V adapter. Per node only `SENSOR_ID` differs.

## Repo layout

```
protocol/   rvl.py (depth codec), frame.py (wire protocol), websocket.py (ws relay),
            control.py (central->node commands, CTL1),
            discovery.py (UDP LAN auto-discovery of central by rig id)
node/       sim_node.py, kinect_node.py (real), background.py (bg subtraction),
            pose.py (MoveNet ONNX 2D pose -> CPOS keypoints, decoupled worker),
            camera_modes.py (depth FOV / color res / fps / align tables, pyk4a-free),
            dump_calibration.py
central/    recorder.py (records synced takes), preview_server.py (live ws relay + control
            fan-out + rig-calib apply/reload + viewer-driven calibration sessions
            + --pose-model central pose fallback for nodes without on-node inference
            + scene recording commands + HTTP /recordings delivery),
            recording.py (scene recording: CPR1 take writer [non-blocking tee]
            /reader + recordings index/delete),
            calibration.py (rig extrinsics from a tracked marker ball: segment_ball
            [largest spherical cluster] + sphere fit + robust Kabsch [RANSAC];
            BallTracker [continuous] + StationaryBallSampler [stop-and-go, default];
            + Tier-1 rough solve, rig_calib.json I/O),
            temporal_denoise.py (per-pixel One-Euro depth low-pass, opt-in
            via --temporal-denoise, defaults still pending real-hardware tuning),
            spatial_denoise.py (edge-preserving bilateral within-frame depth
            smoothing, opt-in via --spatial-denoise; companion to temporal)
processing/ mesh_take.py (take -> depth-grid PLY mesh)
scripts/    run_demo.py (hardware-free spine demo), preview_client.py (headless ws test),
            send_command.py (send control commands to the relay),
            calibrate_rig.py (marker-ball wand pass: collect + solve + rig_calib.json)
deploy/     kinect-node.service (+ .default env + install-node-service.sh):
            run the Jetson node as a boot-time, auto-restarting systemd service;
            run-node.sh + profiles/{orin,nano,default}.env: repo-stored
            device-class default flags, auto-detected from the L4T release
tests/      test_rvl.py, test_background.py, test_camera.py, test_imu.py,
            test_extrinsic.py, test_discovery.py, test_calibration.py, test_rig.py,
            test_pose.py, test_grid.py (CPV1 grid block / mesh connectivity),
            test_sender.py (per-viewer ClientSender mailbox),
            test_recording.py (CPR1 round-trip, non-blocking tee, HTTP endpoint),
            test_xr_pose.py (xr_pose passthrough fanout/sid),
            test_temporal_denoise.py (EXPERIMENTAL per-pixel One-Euro depth filter),
            test_spatial_denoise.py (EXPERIMENTAL edge-preserving bilateral depth filter),
            test_ingest_freshness.py (relay drop-stale-on-ingest: freshest wins,
            recording keeps all), test_relay_workers.py (--workers parallel
            unproject+build == sequential bytes)
docs/       hardware.md, protocol.md, preview_protocol.md, realtime_architecture.md,
            rig_calibration.md (marker-ball extrinsic calibration: procedure + wiring plan),
            skeleton_pose.md (2D pose -> 3D joints: model choice, CPOS wire format, skeleton align),
            crypt_viewer_handoff.md (initial CLAUDE.md for the `crypt` repo),
            crypt_viewer_updates.md (ongoing one-way change log for the viewer),
            kinect_data_improvements.md (catalog of relay post-processing ideas:
            per-sensor cleanup, seam/fusion quality, recording-only heavy passes —
            come back to it before starting new data-quality work), jetson_setup.md
takes/      recordings (gitignored)
```
The browser **viewer is NOT here** — it lives in the `crypt` repo and consumes
`docs/preview_protocol.md`. The Jetson pulls this repo and runs only `node/` +
`protocol/`; it never runs the central server or the viewer.

**Cross-repo handoff workflow.** The user works the `crypt` repo in parallel and
its `CLAUDE.md` evolves there, so **never ship a replacement `CLAUDE.md`** for
it. Instead, append a dated entry to `docs/crypt_viewer_updates.md` describing
any protocol/viewer-facing change (with a concrete snippet), then merge to `main`
and hand the user that file to upload manually into `crypt`. The viewer agent
reads the entries and folds them in.

## How to run

```bash
# Hardware-free spine test:
python3 scripts/run_demo.py --sensors 4 --frames 15

# Codec tests (numpy fast path == pure-Python reference, bit-identical):
python3 -m tests.test_rvl

# Live preview, no hardware/browser (3 terminals): relay, sim node, headless client:
python3 -m central.preview_server                     # downsample now on the node
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 0 --preview-stride 2
python3 -m scripts.preview_client --frames 30
# (real browser viewer = the `crypt` repo; speaks docs/preview_protocol.md)
# Real cam, faster + metric (node sends its own intrinsics; no --calib needed):
#   python3 -m node.kinect_node --host LAPTOP --port 9000 --sensor 0 --frames 0 --preview-stride 2 --profile
#   python3 -m central.preview_server
# Auto-find central by rig id (no fixed IP; survives the laptop's DHCP changing):
#   python3 -m central.preview_server                       # answers discovery by default
#   python3 -m node.kinect_node --host auto --sensor 0 --frames 0
# Discovery tests (query/reply encode + loopback round-trip):
python3 -m tests.test_discovery

# Live control (capture a background plate on all nodes without a browser):
python3 -m scripts.send_command --port 8080 capture-bg --frames 60
# Live camera controls (pick which Kinect data to send; stream adapts):
python3 -m scripts.send_command --port 8080 set-camera --align depth_to_color
python3 -m scripts.send_command --port 8080 set-camera --depth-mode WFOV_UNBINNED
# Camera-mode logic tests (pyk4a-free):
python3 -m tests.test_camera
# IMU / gravity path tests (CIMU round-trip + optical->view + CPV1 block):
python3 -m tests.test_imu
# CPV1 grid block (depth-grid indices the viewer meshes from):
python3 -m tests.test_grid

# Rig extrinsic calibration (docs/rig_calibration.md). Headless dry-run: two
# posed sim nodes share a synthetic ball, the solve must recover the pose:
python3 -m central.preview_server                          # watches rig_calib.json
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 0 --ball 0.05 --pose "0,0,0,0"
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 1 --frames 0 --ball 0.05 --pose "50,1.2,0.15,-0.6"
python3 -m scripts.calibrate_rig --seconds 12 --ball-radius 0.05   # writes rig_calib.json; relay auto-reloads
# Real pass: background captured on all sensors, ball the only foreground,
# --seconds 30 and the REAL ball radius. Or drive it from the viewer's
# Fine Align button / headless:
python3 -m scripts.send_command --port 8080 calibrate-fine --seconds 30 --ball-radius 0.05
python3 -m scripts.send_command --port 8080 calibrate-rough           # Tier-1, zero props
python3 -m scripts.send_command --port 8080 calibrate-floor           # level each camera to its floor
python3 -m tests.test_rig      # trackers/gates, rough+floor solves, file round-trip

# Skeleton pipeline (docs/skeleton_pose.md) headless: posed sims emit synthetic
# pose keypoints; Rough Align auto-upgrades to the skeleton solve:
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 0 --ball 0.06 --pose "0,0,1.0,0.6,-5" --skeleton
python3 -m node.sim_node --host 127.0.0.1 --port 9000 --sensor 1 --frames 0 --ball 0.06 --pose "50,1.2,1.1,-0.6,-8" --skeleton
python3 -m scripts.send_command --port 8080 calibrate-rough      # -> tier "skeleton"
python3 -m tests.test_pose     # CPOS round-trip, JointTracker, solve_skeleton
# Central pose fallback (skeletons for nodes that can't infer, e.g. the Nano):
python3 -m central.preview_server --pose-model models/movenet.onnx

# Scene recording (record the live stream at the relay, replay in the viewer):
python3 -m scripts.send_command --port 8080 record-start --name "my take"
python3 -m scripts.send_command --port 8080 record-stop      # waits for "saved"
python3 -m scripts.send_command --port 8080 list-recordings
curl http://127.0.0.1:8080/recordings                        # same index, HTTP
curl -O http://127.0.0.1:8080/recordings/<id>                # the CPR1 take
python3 -m tests.test_recording

# Temporal depth denoise: ON BY DEFAULT (negligible fps cost, only helps).
# The relay runs it automatically — no flag needed:
python3 -m central.preview_server
python3 -m central.preview_server --no-temporal-denoise   # turn it OFF
#   tune: --denoise-min-cutoff 1.0 (lower = smoother at rest)
#         --denoise-beta 0.01      (higher = less lag on real motion)
python3 -m tests.test_temporal_denoise

# Spatial (within-frame) depth denoise — edge-preserving bilateral, companion
# to the temporal filter above; compose them or run either alone. Off by
# default, one flag, no node change:
python3 -m central.preview_server --spatial-denoise
#   tune: --spatial-radius 1        (1 = 3x3 window, 2 = 5x5 = stronger)
#         --spatial-sigma-depth 30  (mm edge threshold; lower protects finer
#                                    edges, higher smooths harder)
python3 -m central.preview_server --temporal-denoise --spatial-denoise  # both
python3 -m tests.test_spatial_denoise

# Real single-sensor capture (recorder + node, localhost):
python3 -m central.recorder --port 9000 --sensors 1 --out takes/real1
python3 -m node.kinect_node --host 127.0.0.1 --port 9000 --sensor 0 --frames 60

# Mesh a recorded take:
python3 -m node.dump_calibration --out takes/real1/calib.json
python3 -m processing.mesh_take --take takes/real1 --calib takes/real1/calib.json --frame 0
# -> takes/real1/mesh/frame_000000.ply  (tune --edge-mm: lower=less webbing, higher=fewer holes)
```

## Environment gotchas (learned the hard way on the Nano)

- **Source-built Azure Kinect SDK** installs incompletely: copy the *generated*
  headers `k4aversion.h` + `k4a_export.h` and the `k4arecord/*` headers into
  `/usr/include/k4a*`, and copy `libk4arecord.so*` into
  `/usr/lib/aarch64-linux-gnu/` (+`ldconfig`). Set `K4A_INCLUDE_DIR=/usr/include`
  `K4A_LIB_DIR=/usr/lib/aarch64-linux-gnu`. Install pyk4a with `--no-deps`
  (system numpy via apt) then `pip install --user typing_extensions`.
- **Python 3.6** (Nano default): `frame.py` is a plain class (no dataclasses) so
  it imports on 3.6; the codebase avoids `time.time_ns()` (3.7+) — uses
  `int(time.time()*1e9)` (node files included). Keep new node/protocol code
  3.6-safe. (`central/preview_server.py` + `protocol/websocket.py` are
  central-only, x86/3.8+ — they don't run on the Nano.)
- **NumPy 2 breaks pyk4a on the Jetsons**: pyk4a is compiled on-device against
  the installed NumPy 1.x, so any later `pip install` that drags in NumPy ≥2
  (e.g. bare `onnxruntime` for the pose model) kills the node with "a module
  compiled using NumPy 1.x cannot be run in NumPy 2.x". Fix/prevention: always
  `pip3 install <pkg> "numpy<2"` on nodes (or rebuild pyk4a). Hit on the Orin
  2026-07-03.
- **Jetson USB**: `sudo sh -c 'echo 256 > /sys/module/usbcore/parameters/usbfs_memory_mb'`
  to stop `libusb errno=12` transfer errors; each Kinect needs its own 5V supply.
  (The `deploy/` systemd unit applies this automatically as a root `ExecStartPre`.)
- **Run on boot / headless**: `deploy/install-node-service.sh` installs the node
  as a systemd service (`Restart=always`, USB-buffer fix, per-device config in
  `/etc/default/kinect-node`) so it auto-starts and relaunches on failure — the
  node has no internal reconnect loop, so systemd is the supervisor. The env file
  defaults `CENTRAL_HOST=auto` (LAN discovery, below) so a changing central DHCP
  IP needs no reconfig. A non-fatal `ExecStartPre` (`deploy/update-node.sh`)
  **fetches + hard-resets the code to `origin/$UPDATE_BRANCH` on every start**
  (toggle `AUTO_UPDATE`), so the headless workflow is push → reboot → runs latest;
  offline just runs the on-disk code. Updates code only — unit/env changes still
  need a re-run of the installer (but the **device-class default flags** live
  in-repo in `deploy/profiles/` and are resolved at launch by
  `deploy/run-node.sh`, so THOSE roll out with the auto-update). `--headless`
  drops the desktop GUI (`multi-user.target`) for more capture headroom: the node
  draws no windows and the Nano is CPU-bound, so the desktop + any connected VNC
  session just steal cycles from RVL/color. **Caveat (confirmed on hardware): the
  closed depth engine needs a GPU/OpenGL context** — as a bare service it dies
  with `depth engine … error code: 204`. So keep `graphical.target` and pass the
  X session into the service via `DISPLAY` + `XAUTHORITY` in
  `/etc/default/kinect-node` (EnvironmentFile); the perf win is then just not
  keeping a VNC client attached, not full headless. See `docs/jetson_setup.md` §9.
- **Kinect cold-boot enumeration (NOT software-fixable here)**: on a cold boot
  the depth camera (`045e:097c`) often doesn't enumerate, so the SDK can't open
  the device (`libusb … unavailable` / `LIBUSB_ERROR_IO` on the BOS descriptor).
  Cause is hardware **power-up ordering** — the camera must be powered/ready
  before the host scans USB. Reliable workflow: boot the Jetson first (Kinect
  barrel-jack power on), then enumerate the camera; the service's `Restart=always`
  grabs it the moment it appears. A per-start USB-reset experiment (autosuspend
  toggle / `authorized` re-enumeration) was **tried and removed** — it made the
  depth cam drop off the bus entirely and crash-loop. **Do not reintroduce
  per-start USB resets.** Confirm the Kinect's own 5V supply + solid-white LED if
  it won't enumerate. See `docs/jetson_setup.md` §9.
- See `docs/jetson_setup.md`.

## Rendering R&D already done (in the `crypt` repo)

Prototyped many ways to render the capture: GL_POINTS sphere-impostors + Eye-
Dome Lighting; Vertex Animation Textures (one-draw-call); per-point PCA-normal
surfel splatting; EWA weighted-splat blending; per-frame Delaunay trimeshes with
interpolated vertex colors. Key learnings: a **fixed-topology depth-grid mesh +
VAT** is the scalable representation; flat per-splat color reads as "tiled
cells" on a solid surface (fixed by EWA blending or a real interpolated mesh);
and the capture's **per-point colors are high quality** (not compressed — that
was a rendering artifact, not the data). Branches: `…-edl`, `…-vat`, `…-mesh`,
`…-deferred`, `…-surfel`, `…-ewa`, `…-trimesh`.

## Roadmap / next steps

Reoriented around the real-time app (full plan in
`docs/realtime_architecture.md`). MVP = **one camera**, live preview +
trigger-record-download.

1. ✅ **M0 — Fast RVL.** Vectorized NumPy `compress`/`decompress`, bit-identical
   to the pure-Python reference with a fallback. Done (see Current status).
2. 🟡 **M2 — Live preview.** ✅ *server side*: `central/preview_server.py` relays
   node frames → `CPV1` point clouds over WebSocket; verified headless with
   `scripts/preview_client.py`. ⏳ *remaining*: the browser three.js/WebXR viewer
   in the **`crypt` repo** (consumes `docs/preview_protocol.md`); optional color.
3. 🟡 **M1 — Control plane.** ✅ central → node command channel (`protocol/
   control.py`, `CTL1`) with browser→relay→node fan-out. Commands:
   `capture_bg/clear_bg/set_bg_margin/set_denoise` (background subtraction),
   **`set_camera`** (live depth FOV mode + alignment; color res/fps accepted),
   `set_imu` (orientation). (The `set_depth` range-clip command was removed — the
   node streams the full range and culls via background subtraction.) ⏳ remaining:
   `arm/record/stop/status` commands (M3 needs them); optional status/echo back
   to the UI (no ack today — feedback is the cloud changing).
4. 🟡 **M3 — Record + download.** ✅ *scene recording* (2026-07-04, see Current
   status): the relay records the live **wire stream** (registered,
   subject-only `CPV1`) on the viewer's Record button, lists takes, and serves
   them over HTTP; the viewer replays them into the running scene — the app's
   record→replay loop is functional end-to-end at preview fidelity. ⏳
   *remaining (original M3)*: node-local **full-fidelity** recording (full-res,
   pre-downsample, for post-processing/TSDF) → per-node HTTP download —
   reuses the same `record_start/stop` command surface when built.
5. **M4 — N nodes.** Node discovery/registry; trigger fans out to all.
6. 🟡 **Phase 2 (M5) — Aligned/fused.** ✅ *calibration method decided + math
   core built*: **marker-ball ("wand") calibration**, not ICP (inward-facing
   circle = no shared surface) and not boards (face ≤2 cameras) — a sphere's
   fitted center is the same 3D point for every camera, so waving a ball
   through the volume gives dense 3D↔3D correspondences → closed-form
   Kabsch per sensor. `central/calibration.py` (fit_sphere with known radius —
   cap-centroid alone is ~r/2 biased toward each camera; pair_tracks;
   solve_rigid; solve_rig) unit-tested synthetically to <0.01°/<1 mm
   (`tests/test_calibration.py`); full procedure + wiring detail in
   **`docs/rig_calibration.md`**. ✅ *wiring built + verified headless
   (2026-07-02)*: `scripts/calibrate_rig.py`, relay `--rig-calib` apply
   (one world frame on the wire, mtime-watched, no viewer change),
   `rig_poses`/`calib_status` JSON → viewer gizmos + status line,
   viewer-driven `calibrate_fine`/`calibrate_rough` sessions, Tier-1 rough
   solve, sim ball/pose mode (ground-truth pose recovered to 0.16°/3 mm
   end-to-end). **Two-tier design** (see the doc): Tier-1 rough = zero-prop
   (IMU roll/pitch + body-centroid track for yaw/XY, ~5–10 cm, enough for
   scene editing); Tier-2 fine = the wand pass (~2–5 mm expected on real
   ToF). ChArUco was evaluated and rejected (board faces ≤2 cameras →
   chaining; weak Z; wrong modality — calibrate in the depth space you
   render). Retroreflective ball in IR = optional segmentation upgrade, same
   math. ⏳ remaining: the real-hardware wand pass (2 Jetsons, real ball
   radius); then **TSDF fusion** (Open3D) → watertight mesh → glTF/meshopt
   export for the renderer.
7. **Phase 3 — creative FX** (particles from capture geometry); **hands as
   particle attractors** — no Kinect Body Tracking needed (x86-only): run
   open-source 2D pose/hands (MediaPipe/RTMPose) on the node's color image
   (Orin has headroom), look up the aligned depth at each keypoint → 3D hand
   positions as a tiny metadata message (plan in `docs/rig_calibration.md`);
   SMPL-X template tracking (approach C) for fixed-topology streamable
   compression.

Deferred (still wanted, off the MVP critical path): **colored mesh** (bake the
now-aligned color into `mesh_take.py` per-vertex output); **efficient color
transport** for N cameras (raw foreground RGB is fine for 1 cam on WiFi but
scales linearly — switch to JPEG/NVENC before 4-cam); Web Worker / Wasm RVL for
browser-side preview decode.

## Open items

- Confirm whether any Brekel export retains structured per-sensor depth (its
  site blocks automated checks) before committing to a fully custom capture.
- RVM GPL-3.0 licensing vs a permissive matting model (BGMv2/MediaPipe/SAM2).
