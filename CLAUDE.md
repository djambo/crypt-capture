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
  accepted (viewer color-res dropdown built 2026-07-06). **Color-resolution — WHERE
  IT HELPS (2026-07-06, corrected):** it adds real face-color DETAIL only in
  `depth_to_color` (there each color pixel IS a streamed point → more res = more
  colored points) and the future textured mesh. In `color_to_depth` the cloud is
  DEPTH-grid sized, so color is capped at the depth resolution — one color per
  depth point regardless of capture res; a higher source only *marginally*
  improves each point's color (better filtering/registration + 4:3 covers the
  depth FOV better than 16:9). It's **free** on that path (identical
  point count/RVL/wire; only USB + the SDK warp cost more), so `1536P` (2048×1536,
  4:3, 30 fps) is a safe Orin capture default that pays off in depth_to_color/mesh.
  `tests/test_camera.py` pins the "grid dims invariant to color res in
  color_to_depth" property. Mode tables are pyk4a-free + unit-tested
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
  **Works under `--wire cpv3` — by emitting CPV2 during the session (2026-07-08,
  perf-fixed 2026-07-09):** cpv3 ships NO relay-side XYZ (the browser unprojects),
  so a calibration session got nothing to segment the ball / track the body from
  → Fine/Rough Align silently did nothing on a cpv3 relay (no captures, no LOCK
  markers). The first fix unprojected the XYZ IN ADDITION to building the cpv3
  payload while a session was active — but that ~2x per-frame work, single-
  threaded and un-drop-stale as a session is, made **fine align lag badly**
  (much worse than the old cpv1/cpv2 path). Fixed properly: while a session is
  active a cpv3 relay **does NOT take the cpv3 branch** — it falls through to the
  normal unproject path and emits **CPV2** (`_finish_frame`, `fmt = "cpv2" if
  wire=="cpv3"`), so ONE unproject feeds BOTH the wire and the ball segmentation
  (the fast pre-cpv3 cost, half the wire of cpv1). The crypt viewer renders those
  CPV2 frames on the CPU **per-frame** (it dispatches on the magic; `gpuSeen`
  tracks the freshest frame's format) and flips straight back to the GPU shader
  when cpv3 resumes at session end — so this is transparent, no reload, no manual
  wire switch. The viewer's default ball radius is **10 cm** (a 20 cm-diameter
  sphere).
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
  **Perf fix — fine align was crawling (2026-07-09):** `segment_ball`'s voxel
  `np.unique(axis=0)` + Python connected-components is SUPERLINEAR in point count
  (measured 26 ms @21k pts, 135 ms @100k, 318 ms @200k), and it runs per-sensor
  per-frame on the relay's single reader thread — so a close-range full-body
  subtracted foreground on 2 cameras cost 150–600 ms/frame, and (see next para)
  the session had drop-stale OFF so the backlog grew unbounded → the operator saw
  a stream seconds behind, effectively frozen. Fix: `segment_ball` stride-
  subsamples its input to `max_input` (default **8000**) points first — the ball
  is located from its spatial CLUSTER, not density, so this is sub-mm-identical
  (validated 200k→8k: <1e-3 mm centre error) at a fixed ~12 ms. A 2-sensor 80k-
  foreground calib feed is now ~25 ms/frame (40 fps ceiling) vs ~220 ms before.
  **Drop-stale now stays ON during a calibration session** (`_serve_node` —
  previously disabled for it): the stationary sampler is window/velocity based,
  so fresh-but-sparse frames sample it correctly and freshness beats density (a
  lagging ball centre is useless). These two + the cpv2-during-session change
  (one shared unproject, next section) are what make fine align usable on cpv3.
  **Fine-AFTER-rough offset bug — feed calibration the RAW cloud (2026-07-09):**
  `_feed_calibration` must get the RAW (pre-rig) view-frame cloud — `solve_rig`
  computes the FULL raw→world transform and the LOCK marker applies the rig ONCE.
  But `_finish_frame` was applying the loaded rig to `xyz` BEFORE returning it,
  so a fine pass run AFTER rough (a rough rig loaded) fed calibration the already-
  registered world-frame cloud. Result: (a) the non-reference sensor's LOCK
  marker got the rough rig applied TWICE → one sphere sat offset sideways during
  the pass; (b) the solve saw two already-aligned tracks, produced a ~identity
  residual, and SAVED that as the whole rig — so it WIPED the rough alignment,
  the camera poses collapsed toward each other (gizmos near-coincident, not their
  real separation), and the clouds sprang apart with a large horizontal offset.
  Fix: `_finish_frame` keeps the raw cloud (`xyz_calib`) and returns THAT as its
  3rd value for the calibration feed; the WIRE still carries the rig-transformed
  cloud. Regression-tested (`tests/test_calib_raw_feed.py`: the fed cloud is
  rig-independent = raw; fails by exactly the translation with the bug).
  **Multi-camera RING coverage — graph-chained solve + keep-prior (2026-07-09):**
  on a 3+-camera ring a ball spot is usually seen by only 2 of N cameras, and
  `solve_rig` paired every sensor DIRECTLY to the reference — so a camera that
  rarely shared the ball with the ref got too few pairs and was UNSOLVED (then
  dropped to RAW → its gizmo collapsed onto the ref, clouds sprang apart; the
  user saw "s0: 0.0mm/7 pairs — UNSOLVED s1,s2"). Two fixes: (1) `solve_rig` is
  now GRAPH-BASED — it builds a pairwise transform for every co-visible pair and
  chains from the reference along a max-inlier-pairs spanning tree, so s1
  registers via s1→s2→ref (star topology reduces to the old direct solve;
  `tests/test_rig_chain.py` proves a ring where s1 shares NO captures with the
  ref registers to ~1e-15 m). (2) `_finish_calibration` KEEPS a sensor's prior
  (e.g. rough) transform when a pass still can't solve it, instead of dropping it
  to raw — frame-consistent because `solve_rig`/`solve_rough` both put the ref
  (min id) at its own gravity-leveled pose (guarded on the ref matching). Those
  kept sensors ride back in the `calib_status` `done` message's new `kept` field
  (the viewer shows "kept sN at prior align — give it ball coverage"), so a
  partial fine pass improves what it can and NEVER regresses the rest.
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
  **Late join (2026-07-10, the 120°-ring fix):** the quorum-2 commit fired the
  instant the SECOND camera settled, and a camera settling later could never
  join that capture — on the real inward 3-camera ring (different distances/
  noise/fps per camera spread settle times; the shelf test masked it) the
  slowest camera missed nearly every capture, its pairwise solve edges accrued
  at ~1/3 the capture rate and never reached `min_pairs=6`, so the fine pass
  solved ONLY the reference and "kept s1, s2 at prior align" even though the
  operator saw all three markers green (green lagged the commit).
  `StationaryBallSampler` now keeps the capture OPEN for `late_join_window`
  (default 2.5 s, command-tunable, ends early when the ball moves): a camera
  that settles after the commit appends its window-averaged sample under the
  SAME capture id — correct because the held ball is at one physical spot
  regardless of sampling instant. `min_still_sensors` is now command-exposed
  too. Feedback: `calib_status.balls` gains `cap` (sample committed into the
  current capture, via `capture_members()`); the viewer turns that camera's
  marker GOLD (`★in` on the status line) — the operator holds until every
  camera that can see the ball is gold. Regression-tested
  (`test_calibration.test_stationary_late_join`: a simulated 120° ring whose
  third camera settles 0.5 s late gets 0 captures without late join, all of
  them with it, and solve_rig at the relay defaults recovers the ring to
  <1°/2 cm of ground truth).
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
  models/movenet.onnx --pose-trt --color-resolution 1536P --workers 4` (safe
  pre-setup: missing model/runtime → "pose disabled", streaming unaffected;
  1536P color = high-quality capture for depth_to_color/mesh, ~free on the
  color_to_depth wire, `--workers 4` = headroom for the heavy depth_to_color
  close-up), nano = full res, 720P, no pose. The
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
  The real full-cloud unlock is the **CPV2 wire format** — BUILT next entry.
- ✅ **CPV2 compact wire format (2026-07-05, `preview_server --wire cpv2`)** —
  the ~52 %-smaller point-cloud wire format that halves bandwidth for EVERY mode
  (a bg-subtracted subject over the VR PC's link: ~114 → ~55 Mbit/s; a full
  un-subtracted sensor: ~1.6 → ~0.8 Gbit/s). Same 20-byte header, same flag
  bits, same block order as CPV1 — the MAGIC (`CPV1`/`CPV2`) tells the viewer how
  to read TWO changed blocks: **positions are uint16-quantised** (a 16-byte
  per-frame quant block — offset xyz f32 + one uniform scale f32 — right after
  the header; dequant `p=q*scale+offset`) and **the grid is a valid-mask
  BITMAP** (`u16 grid_w,grid_h` + `ceil(gw*gh/8)` LSB-first bits; set bits in
  order are 1:1 with positions, so the viewer rebuilds the exact CPV1 index list
  and meshes unchanged) instead of `count × u32` indices. rgb/gravity blocks are
  byte-identical to CPV1. **Not lossy in any meaningful sense:** the per-frame
  bounding-box scale makes the quantum `max_span/65535` ≈ **0.03 mm** (2 m
  subject) to **0.12 mm** (8 m room) — 30–60× below the Kinect's ToF noise, i.e.
  below the jitter temporal-denoise is already smoothing (the user's constraint:
  compress below the noise floor, never below real sensor resolution). Positions
  round-trip to <0.07 mm in tests. **DEFAULT stays `cpv1`** so a relay restart
  never breaks a running viewer; **the crypt viewer now decodes CPV2**
  (`cpv1.js` `parseFrame` dispatches on the magic, 2026-07-06), so `--wire cpv2`
  is safe to flip once relay+viewer are both current. Full format in
  `docs/preview_protocol.md`. Recordings made under cpv2 hold cpv2 frames (the
  CPR container just wraps CPV messages — the RecordingPlayer uses the same
  parser). `build_message(fmt=…)` branches; `_quantize_positions` +
  `_build_message_v2` are the encoder. Unit-tested (`tests/test_cpv2.py`:
  quantum-below-noise, CPV1-equivalent positions within one quantum, bitmap ==
  unproject indices, ~49 % size, empty frame) + E2E (relay `--wire cpv2` + sim
  node → reference decoder gets valid rgb+grid frames). ⏳ Next lever if 3-cam
  full-cloud is ever needed on <2.5 GbE: per-client format negotiation (mixed
  old/new viewers) and/or shipping quantised DEPTH + intrinsics so the browser
  unprojects (≈5 B/pt) — the **`CPV3` / browser-GPU-unproject** plan,
  `docs/gpu_unproject.md`.
- ✅ **Numba RVL decode — approach C (2026-07-06)**: RVL decode is inherently
  SEQUENTIAL (variable-length bit codes — a pointer chase NumPy can't fully
  vectorize), the one relay stage neither the `--workers` pool nor the GPU
  helps. `protocol/rvl.py` now has an optional **Numba `@njit`** decode
  (`_decompress_numba`) behind the same import-guarded fallback as the NumPy
  path (numba present → JIT, else NumPy, else pure-Python — all **bit-identical**,
  cross-checked in `tests/test_rvl.py`). Measured (numpy2 box): full 720p decode
  **96 → 9.4 ms (~10×)**; a subtracted subject is already cheap (~0.2 ms, ~1.5×)
  — so C is a **full-cloud / setup-view** win, not a subject-path one (the
  subject was never relay-bound). `@njit(cache=True)` caches the compiled code, so
  only the first frame after a relay start pays ~1-2 s compile. **Optional relay
  dep** (`pip install numba`); the Jetson ENCODE path stays on NumPy (already
  fast, numba on ARM is heavier). This also stays on the critical path of the
  A-lite `CPV3` design (the relay still decodes RVL there), so it isn't throwaway.
  Bit-fix hard-won: numba widens `uint32 << n` to signed 64-bit, so the
  reference's `& _U32` wrap needs explicit `uint32()` casts on the shifts.
- 🟡 **Approach A — CPV3 relay encoder BUILT (2026-07-06), viewer next**:
  `preview_server --wire cpv3` ships **depth + valid-mask bitmap + a `step`
  block** and a per-sensor **`sensor_calib`** JSON (depth/colour intrinsics +
  distortion + rig) instead of unprojecting — the relay does NO per-point
  unproject/UV/rig (the whole CPU + wire win). `build_message_v3` /
  `extract_depth_grid` (shares unproject's exact stride/mask logic). Proven
  **lossless** — reconstructs CPV1's exact XYZ incl. stride + rig — at ~2.5 B/pt
  vs 19 (`tests/test_cpv3.py` + socket E2E: relay+sim → CPV3 frames +
  sensor_calib). Default stays cpv1; `--wire cpv3` needs the (not-yet-built)
  browser GPU-unproject shader, so it's opt-in. Calibration sessions still need
  cpv1/cpv2 (they consume relay-side XYZ; cpv3 returns none — a one-time setup
  step). ✅ **Viewer CPV3 path BUILT (2026-07-06)** in `crypt` (`cpv3.js` +
  `LivePointCloud`): parses CPV3, consumes `sensor_calib`, and unprojects into
  the CPV1 frame shape the existing renderers consume — so cpv3 renders like
  cpv1 (math validated headlessly). Runs in JS on the CPU for now (fine for
  desktop/PCVR; wire win already helps standalone); the **GPU-shader swap** to
  relieve a weak mobile CPU is the remaining optimization. ✅ **CPV3 per-point
  rgb (2026-07-07):** CPV3 now ships an rgb block (`FLAG_RGB`, same order as
  depth — `extract_depth_grid(color_grid=…)` + `build_message_v3(rgb=…)`), so the
  cpv3 POINT render (CPU and GPU) is coloured **frame-locked** to the geometry
  like cpv1/cpv2. This fixes "colour lags/swims behind depth" on the GPU point
  path: the JPEG texture is an async-decoded separate stream (fine for the mesh,
  where colour interpolates across triangles, but on discrete points a 1-frame
  texture lag reads as colour sliding off the moving surface); per-point rgb has
  none of that. The viewer's GPU mode no longer requests the texture at all
  (`syncTexture` = mesh-only), so it also drops the node JPEG-encode cost + the
  set_texture flip-flop. rgb adds ~3 B/pt (cpv3+rgb ≈ 29 % of cpv1 vs 13 %
  without — still ~3.4× smaller than cpv1). `tests/test_cpv3.py::test_cpv3_carries_rgb`.
  Remaining gaps: RecordingPlayer skips cpv3 takes; calibrate in cpv1/cpv2.
- 📋 **Approach A — browser-GPU unprojection (design, `docs/gpu_unproject.md`)**:
  the architectural fix for relay CPU + wire on full clouds. Stop shipping XYZ;
  ship compact **depth + grid bitmap + calibration uniforms** and unproject on
  the CLIENT GPU (a vertex/compute shader: ray×depth → world via the rig matrix,
  in-shader UV projection, mesh from the bitmap). **A-lite** (relay keeps
  RVL-decode+denoise, only stops the per-point XYZ expansion) is the chosen
  variant — ~9× smaller wire (~2 B/pt), big relay-CPU drop, no Wasm-RVL/shader-
  denoise needed; **full-A** (RVL+denoise in-browser) deferred until measured
  necessary. New additive `CPV3` magic (default stays cpv1); the viewer already
  dispatches on magic. Sequencing: C ✅ → CPV3 relay encoder → viewer GPU shader
  → WebTransport (approach D). NOT YET BUILT. **PCVR vs standalone = ONE
  architecture + per-client LOD, not a branch** (doc "Tiers" section): GPU
  unproject is cheap even on a mobile Adreno; standalone's real limits are Wi-Fi
  bandwidth + mobile-CPU parse, both of which A-lite *reduces* (~9× wire). Tier
  by payload — grid density, fps, subject-only, and the texture codec byte
  (PCVR→JPEG, standalone→H.264/H.265 via WebCodecs HW decode); the textured mesh
  is the standalone-friendly render. Same shader for both.
- ✅ **Relay latency-adaptive frame retirement (2026-07-05)** — the parallel
  `--workers` path used to hold `workers-1` frames in flight before emitting the
  oldest, so on `--workers auto` (4-8) it added **~100-260 ms of pure latency**
  to the cloud even when the pool had headroom (a light single-camera subtracted
  subject) — the cloud visibly lagging behind the viewer's dead-reckoned
  (motion-predicted) skeleton. Fixed in `_serve_node`: after each submit, emit
  every future that is ALREADY `.done()` (strictly in order) BEFORE the
  window-full block, so when per-frame work < the ~33 ms inter-frame gap the
  previous frame retires within ~1 frame; the `len(inflight) >= workers` block
  now only kicks in when the pool is the real bottleneck (heavy full-room),
  preserving throughput mode. Output bytes/order unchanged (drop-stale still
  keeps the INPUT fresh; this keeps the OUTPUT prompt). Measured: workers=6,
  light 3 ms stage @30 fps → mean emit latency 168 → 34 ms. NB the cloud is
  still capped at the Kinect's **30 fps** and, unlike the skeleton, is NOT
  motion-predicted (a point cloud has no per-point identity across frames to
  extrapolate — that needs template/mesh tracking, approach C), so the skeleton
  will always *lead* on fast motion; this removes the avoidable relay latency,
  not that inherent asymmetry. `tests/test_relay_workers.py` (parallel ==
  sequential bytes) + `tests/test_ingest_freshness.py` still green.
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
- ✅ **Relay per-stage observability + temporal-denoise bbox crop (2026-07-09)**
  — root-caused "3 cameras cap at ~22–27 fps in with stale skips even at ~700
  pts" (the first real 3-camera subject captures). Observability: the
  per-sensor stats line now prints per-stage ms (`dec` RVL decode / `den`
  temporal+spatial denoise / `col` ray-table+color-grid / `fin`
  unproject+build), a startup line prints WHICH RVL decoder this process
  resolved (`rvl.decoder_name()` — numba/numpy/python; the import guard is
  silent, so "installed on the box" was unverifiable), and per-viewer sender
  backlog drops (`ClientSender.dropped`, previously invisible in every log)
  are printed when they change. Reading rule: `fps in + stale skipped` ≈ the
  node's actual send rate — skips > 0 means the RELAY is the bottleneck, and
  the stage times say which stage. **The measured culprit: the temporal
  One-Euro filter ran ~15 full-grid float passes (~16 ms/f on a fast x86)
  REGARDLESS of subject size** — the reader thread's dominant serial cost.
  `TemporalDepthFilter.filter` now CROPS all per-pixel work to the
  valid-pixel bounding box — exact, not approximate: an invalid pixel's
  state is frozen by construction (x_hat/dx_hat/last_valid_t untouched,
  output 0), so out-of-box pixels need no work; only `valid_prev` needs a
  full-grid memset. Measured: 16.6 → 0.4 ms/f empty, 0.9 ms @20k-pt subject
  (full room = whole-grid box = old cost, and drop-stale already covers that
  mode). Also `unproject`/`extract_depth_grid` sub-grid selection switched
  from double fancy-index copies (`d[vs][:, us]` — two full-grid copies per
  frame even at stride 1) to strided slice VIEWS (byte-identical output;
  `fin` ~6 → ~1-1.6 ms). Net serial chain @subject: **~18 → ~3 ms/f**. All
  relay/denoise/grid/cpv2/cpv3/texture/extrinsic/imu tests green (incl. the
  parallel==sequential byte-equivalence and extract==unproject invariants).
  **Second culprit, found by the new stage times (fps stayed ~25 with ~6 ms/f
  of work): the drop-stale gate itself.** It skipped on `select()` alone —
  "any pending BYTES" — so ONE early byte of a partially-arrived next frame
  made the reader discard the complete frame in hand and then BLOCK inside
  read_message until the rest trickled in: ~15-20% of frames thrown away
  under ordinary arrival jitter with the reader mostly idle. Fix:
  `protocol/frame.py message_buffered(sock)` (MSG_PEEK, knows every magic's
  framing) — the drain now skips ONLY when a COMPLETE newer message is
  already in the local receive buffer (`tests/test_ingest_freshness.py::
  test_partial_message_does_not_trigger_skip`). Companion: node sockets get
  an 8 MB SO_RCVBUF — drop-stale can only skip what's LOCALLY buffered, and
  the OS default (64 KB on Windows) couldn't hold even one full-room frame
  (~1.1 MB RVL+RGB), so a backlog used to live on the NODE side (its sendall
  blocked → its capture loop parked) where the freshness rule couldn't see
  it.
- ✅ **Node colour-exposure control + clock pinning (2026-07-09)** — after the
  relay fixes, one camera (sensor 2) still wandered 24–30 fps while the others
  held 30, with similar low `sat%` on all nodes. Diagnosis: **auto-exposure**
  — in a dim view the colour camera picks the next flicker-safe exposure step
  ABOVE 33.3 ms (40 ms at 50 Hz mains → 25 fps colour), and
  `synchronized_images_only` drags the whole capture down with it; the camera
  facing the dim side of the room flips between 30 ms/40 ms as the scene
  brightness wobbles (the 24↔30 fps signature). Nothing in the pipeline set
  exposure before. New `kinect_node` flags (applied after every sensor
  (re)start, best-effort): **`--exposure <µs>`** (manual; also equalises
  colour across the rig — a fusion win) and **`--powerline 50|60`**
  (anti-flicker mains frequency; Europe = 50, SDK default 60). **The firmware
  snaps exposure to a step table that DEPENDS on the powerline frequency**
  (60 Hz: …16670, 33330; 50 Hz: 10 ms multiples …20000, 30000, 40000) — the
  node logs the ACTUAL value chosen and warns when it lands above 33.3 ms
  (sub-30 fps). Longest 30 fps-safe step: **30000 at 50 Hz**, 33330 at 60 Hz.
  Recommended EXTRA_ARGS for this rig: `--powerline 50 --exposure 30000`
  (raise room light rather than exposure if too dark — hit on hardware:
  requesting 33330 at 50 Hz snapped up to 40 ms and pinned that camera at a
  consistent ~25 fps).
  Companion: the systemd unit now runs **`jetson_clocks`** as a root
  ExecStartPre (non-fatal) — the default schedutil governor parked cores at
  729 MHz–1.2 GHz between bursts, slowing the worker stage mid-frame
  (intermittent `sat N%` without true saturation). Unit changes need one
  `sudo deploy/install-node-service.sh` re-run per device; the flags roll out
  via EXTRA_ARGS (or a future profile default).
- ✅ **Scene-coherent broadcast + capture-time timestamps (2026-07-09, the
  "sync track" first half)** — the multi-camera body used to refresh in
  pieces: each sensor's frame was broadcast the moment it finished, so parts
  repainted at each camera's own arrival phase. Now (1) `kinect_node` stamps
  `Frame.timestamp_ns` at CAPTURE (`tc`), not send — send time was biased by
  per-frame queue/worker latency; and (2) the relay's **`SceneBundler`**
  (default ON, `--no-scene-sync` to disable) holds each sensor's freshest
  finished frame and releases ALL sensors' frames TOGETHER — barrier with a
  METRONOME deadline (`--scene-sync-timeout`, default 36 ms, anchored to the
  PREVIOUS FLUSH = a hard floor on the scene rate): a bundle flushes the
  instant every ACTIVE sensor contributed (zero added latency beyond the
  cameras' own phase spread) or at last_flush+timeout — a late camera then
  just misses that bundle instead of dragging the scene rate down (the
  first-frame-anchored 45 ms version measured ~24 bundles/s @ 75% on the
  healthy 3×30 fps rig: every late frame stretched the cycle while punctual
  cameras' waiting frames were overwritten). A sensor silent >1.5 s leaves
  the barrier, single-sensor rigs flush per frame (old behaviour), newer
  frames overwrite pending slots (freshness rule), 3 ms grace after a
  bundle's first frame covers resume-from-idle. Bundles go to each viewer as one
  atomic `ClientSender.put_frames` batch → back-to-back WS messages → the
  viewer (which already applies its whole stash per rAF) repaints the whole
  scene in one rendered frame — NO viewer change needed. Recording still
  tees every frame per-sensor in `_emit` (not bundled). The relay logs a
  sync-quality line every 5 s: bundles/s, % complete, ARRIVAL spread
  (relay clock — reliable) and CAPTURE spread (node clocks — meaningful once
  the Jetsons share NTP/chrony). **Without sync cables** capture instants
  still differ by up to ~16 ms (physics) but presentation is coherent — the
  "good result without cables" mode; **with the 3.5 mm cables**
  (`--sync master/sub --sub-delay-us 160*i`, which also removes inter-Kinect
  ToF interference) captures become simultaneous and the logged capture
  spread drops to ~0 — same code path, no reconfig, the operator SEES the
  cables working. `tests/test_scene_sync.py` (complete-flush latency,
  timeout flush, freshest-wins overwrite, single-sensor immediacy, dead-
  sensor barrier decay); `test_relay_workers` pins the compute path with
  scene_sync=False (byte equivalence is orthogonal to delivery timing).
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
- ✅ **Textured mesh — full-res colour on cheap geometry (2026-07-06, design:
  `docs/textured_mesh.md`)**: the real fix for soft facial colour. Colour was
  carried **per point**, so colour detail was bolted to geometry resolution —
  `color_to_depth` caps it at the depth grid, `depth_to_color` lifts it only by
  exploding the point count (heavy at every stage, tested UNUSABLE close-up).
  Now the subject **mesh** keeps cheap `color_to_depth` geometry but carries
  colour as **one full-resolution JPEG per frame + per-vertex UVs**, sampled
  per-fragment in the viewer → colour at the colour camera's full resolution on
  a ~640×576 mesh, at 30 fps. Only helps the mesh (a point is one fragment).
  **Pipeline:** node (`kinect_node`) JPEG-encodes `cap.color` (cv2/nvjpeg, else
  Pillow) and sends it as `CTEX` + colour intrinsics/distortion + DEPTH→COLOR
  extrinsic as `CCLR` (`protocol/frame.py`, additive messages); the **relay**
  projects each depth point into the colour image for its UV
  (`preview_server._project_color_uv`, forward Brown-Conrady — the inverse of
  the ray table) and adds `FLAG_UV`(0x10) + `FLAG_TEXTURE`(0x20) blocks to the
  `CPV1`/`CPV2` frame (rgb still sent → old viewers/point render unchanged); the
  **viewer** (`MeshCloud`) uploads a UV attribute + async-decodes the JPEG
  (`createImageBitmap`, latest-wins) into the mesh albedo, dropping vertexColours
  while textured. Off by default end-to-end — the viewer sends `set_texture`
  only while the subject render is `mesh` (JPEG encode is pure cost otherwise);
  `color_to_depth` only. UVs computed at the RELAY (x86, already unprojects) so
  the CPU-bound Jetson stays out of it; JPEG codec byte on the wire so NVENC can
  slot in later. **Perf (hard-won 2026-07-06):** JPEG encode must run on a
  DEDICATED THREAD, not the capture thread — inline it halved node fps 30→15 (the
  ~15-25 ms encode blocked every capture; confirmed on hardware). Now the capture
  thread only copies the latest colour into a slot (latest-wins) and the encoder
  thread does the work in parallel (`jpeg_encoder` in kinect_node). Still: fewer
  points / lower colour res / lower `quality` all reduce the cost further.
  Unit-tested (`tests/test_texture.py`: CCLR/CTEX round-trip, UV projection
  recovers pixels, CPV1+CPV2 UV/texture blocks) + socket E2E (relay + `sim_node
  --set_texture` synthetic texture → headless client gets UV in [0,1] + the
  texture block) + viewer parse validated for both wire formats.
  **Colour↔depth sync fix (2026-07-07):** the texture was visibly LAGGING the
  geometry — on a hand wave the mesh moved fast and the colour bled/smeared as
  it caught up. Cause: the node encodes JPEGs on a SEPARATE latest-wins thread,
  so a `CTEX` no longer arrives right before its `Frame` (it lags by the
  ~15-25 ms encode + transport), yet the relay paired each frame with the
  **latest received** texture and `pop`ped it — so most geometry frames got a
  stale texture or NONE (the viewer then froze the last one), and the skew GREW
  under drop-stale ingest (which drops depth frames while `CTEX`s pile up). Fix:
  both messages already carry the same capture `frame_id` (depth `sent`, texture
  `tex_slot["fid"]=sent`), so the relay now **buffers recent textures keyed by
  fid** (`_pending_texture` = per-sensor deque, `TEXTURE_BUFFER=16`) and pairs
  each geometry frame with its **nearest-fid** texture (`_take_texture`, prunes
  forward-only) instead of "latest received" — colour and depth shown together
  are the same capture instant (residual ≤~1 frame because depth's pool latency
  can outrun its own texture's arrival; bounded, not the old growing smear). No
  node/wire change; unit-checked (nearest-fid + forward-prune) + all texture/
  cpv2/cpv3/grid/recording tests green. ⏳ next: hardware tuning (encode cost,
  quality); optionally drop rgb in textured mode for bandwidth; NVENC/H.26x
  colour transport for many viewers.
- ✅ **IR colour mode (2026-07-10, `set_ir`)** — render the Kinect's ACTIVE-IR
  image as the point colours. New forwarded control command
  `{"cmd":"set_ir","enabled":bool}` (viewer "IR colour" toggle /
  `send_command set-ir --enabled on|off`): the node substitutes tone-mapped IR
  grey for the camera colour in `_process_frame` — the IR image
  shares the depth camera's geometry (same grid, same valid mask;
  `cap.ir` in color_to_depth, `cap.transformed_ir` in depth_to_color — the
  latter needs a pyk4a exposing it, else the node logs once and stays on
  colour), so the swap is exact per point and the **wire format is unchanged**.
  **The white point is AUTO-GAINED (2026-07-10, same day):** a fixed full-scale
  (first cut `IR_CLIP=1000`, k4aviewer's range) rendered the whole subject
  WHITE on hardware — active-IR spans orders of magnitude with distance/
  reflectivity (skin at 1-2 m returns thousands). Now each frame's worker
  measures the subject's 99th-percentile IR (`IR_WHITE_PERCENTILE`), returns it
  in the result tuple, and the sender EMA-smooths it (`IR_SCALE_EMA=0.1`, no
  gain flicker) into `ir_mode["scale"]`, which the capture thread hands to the
  NEXT frame's worker — classic auto-exposure with one frame of lag (worker
  processes can't share state). Sqrt curve on top for shadow detail;
  `IR_SCALE_FLOOR=200` so an empty scene's noise isn't amplified; the scale
  resets on every set_ir toggle (re-expose fresh)
  (the rgb block just carries grey → cpv1/cpv2/cpv3, recordings, the env plate
  and every viewer render follow automatically). While IR is on the node skips
  the SDK colour warp unless the pose worker needs the colour image; node-side
  pose still gets real colour (the central pose fallback would see grey —
  known, acceptable). The viewer stops requesting `set_texture` while IR is on
  (the JPEG stays RGB and would mismatch the mesh). `sim_node` acks `set_ir`
  by collapsing its gradient to luminance grey (R==G==B) so the path is
  headless-verifiable. Unit-tested (`tests/test_ir.py`: tone map, the
  _process_frame IR branch incl. stride + mask pairing, sim grey) + E2E (relay
  + sim + WS client: rgb flips coloured → grey → coloured on toggle).
- ✅ **Background capture at CAMERA rate + plate-done ack (2026-07-10)** — two
  fixes for "Capture Background almost never works over WiFi". (1) The node's
  capture loop parks when the send queue is full, so on a WiFi-choked link
  (~4 fps with stalls) it also CAPTURED at the wire rate — `capture_bg`'s
  60-frame average took 15–60+ s with subtraction disabled the whole time,
  while the viewer's label optimistically claimed "done" at 2.5 s. Now while
  `bg.capturing` the saturated branch still reads the camera and feeds the
  plate (dropping the frame for sending — `get_capture` blocks until the next
  camera frame, no GIL spin; a couple of vectorized adds per frame for the
  ~2 s the capture lasts), so the plate always completes in `frames/30` s of
  wall time. (2) New fixed-size **`CSTA` node→central status message**
  (`protocol/frame.py` `encode_status`/`STATUS_BG_CAPTURED`; in
  `_FIXED_MESSAGE_SIZE` so `message_buffered`/drop-stale handle it): the node
  queues it the moment the plate finalises (from the normal path, so a full
  outq can't block on it), the relay rebroadcasts it to viewers as
  `{"type":"node_status", sensor, event:"bg_captured"}` (handled in
  `_handle_node_control`, so a status in a drained backlog still applies) —
  the crypt viewer shows truthful per-camera "Background set on N/M
  camera(s)" instead of the blind timer (which stays as the old-node
  fallback). `sim_node` acks capture_bg with the same CSTA so the path is
  headless-testable. Unit (`test_background.test_status_message_round_trip`)
  + E2E (sim → relay → WS client: capture_bg → node_status received).
  **Node-side change — needs the push→service-restart flow to reach the
  Jetsons.**
- ✅ **Subject-only realtime workflow + plate persistence (2026-07-10, same
  push)** — the user's call: *nobody needs the full environment in realtime*
  (the viewer freezes ONE frame per camera as the static room reference), so
  the node can throttle the un-subtracted full room to **`--setup-fps`**
  and switch to **full-rate streaming only once
  background subtraction is active** (the subject-only frames are tiny, so
  30 fps fits any link). **Default 0 = never throttle — OPT-IN** (first
  deployment shipped default 2 and the pre-capture 2 fps view read as "the
  Kinects broke"/unusable on the rig, 2026-07-10 evening; enable per-device
  via EXTRA_ARGS when a link truly can't carry the setup view). The throttle
  sleeps WITHOUT touching the SDK (camera's internal queue keeps discarding)
  and is bypassed while `bg.capturing` (the plate still averages at camera
  rate). Companion fix — **the plate now PERSISTS across node restarts**
  (`background.py save/load`, `<tmp>/kinect_bg_plate_<sensor>.npz`): it used
  to live only in process memory, so every systemd relaunch (WiFi stall
  killing the socket) silently dropped subtraction and the stream fell back
  to the full room — "capture worked, then stopped working". Reloaded on
  start (shape-checked; a camera-mode change discards it), saved on every
  completed capture, deleted by `clear_bg`; a successful reload re-sends the
  CSTA ack. The relay **caches the last node_status per sensor and replays
  it to each new viewer on connect** (cleared on clear_bg) so panels show
  the true subtraction state after a reload. The node stats line prints
  `bg ON/capturing/off (setup rate)` for journalctl diagnosis.
  `test_background.test_plate_persistence`; E2E re-verified.
- ✅ **Relay TLS / wss:// (2026-07-07)**: `preview_server --tls-cert <pem>
  --tls-key <pem>` serves the browser port over **wss://** (+ https:// for the
  `/recordings` endpoint), so a standalone headset on an https:// page (the
  `npm run dev:https` viewer — WebXR needs a secure context) connects without a
  mixed-content block, no `adb reverse` needed. TLS is terminated per-client in
  `_serve_client` (`ssl.SSLContext(PROTOCOL_TLS_SERVER)` wraps each accepted
  socket, off the accept loop) so a slow handshake never stalls other viewers;
  plain ws:// is unchanged when the flags are absent. A self-signed cert works
  (`openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem
  -days 365 -subj '/CN=<relay-ip>'`; accept it once in the headset browser).
  Verified live: HTTPS `/recordings` 200 + a full wss upgrade (101 Switching
  Protocols) over TLS; non-TLS to the port is refused.

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
            unproject+build == sequential bytes),
            test_cpv2.py (CPV2 compact wire: quant-below-noise, CPV1-equivalent
            positions, bitmap grid == unproject indices, size),
            test_texture.py (textured mesh: CCLR/CTEX round-trip, UV projection,
            CPV1+CPV2 uv/texture wire blocks),
            test_cpv3.py (CPV3 GPU-unproject wire: extract==unproject grid,
            lossless XYZ reconstruction incl. stride+rig, size vs cpv1),
            test_calib_raw_feed.py (calibration is fed the RAW pre-rig cloud, so
            a fine-after-rough pass solves the full transform not a ~identity
            residual — the collapsed-gizmos / clouds-spring-apart regression),
            test_rig_chain.py (graph-chained solve_rig registers a 3-camera ring
            where a sensor shares NO ball captures with the reference),
            test_ir.py (IR colour mode: tone map + the _process_frame IR branch
            behind a pyk4a stub + sim_node grey)
docs/       hardware.md, protocol.md, preview_protocol.md, realtime_architecture.md,
            textured_mesh.md (full-res colour on cheap geometry: JPEG texture +
            per-vertex UVs, relay UV projection, the CCLR/CTEX/set_texture wiring),
            gpu_unproject.md (approach A: ship depth+calib, unproject on the
            client GPU — CPV3 wire + shader plan; approach C = Numba RVL decode),
            rig_calibration.md (marker-ball extrinsic calibration: procedure + wiring plan),
            skeleton_pose.md (2D pose -> 3D joints: model choice, CPOS wire format, skeleton align),
            kinect_data_improvements.md (catalog of relay post-processing ideas:
            per-sensor cleanup, seam/fusion quality, recording-only heavy passes —
            come back to it before starting new data-quality work), jetson_setup.md
takes/      recordings (gitignored)
```
The browser **viewer is NOT here** — it lives in the `crypt` repo and consumes
`docs/preview_protocol.md`. The Jetson pulls this repo and runs only `node/` +
`protocol/`; it never runs the central server or the viewer.

**Cross-repo work (both repos are in the session).** `crypt` and `crypt-capture`
are checked out side by side (`/home/user/crypt` + `/home/user/crypt-capture`)
and editable together in one session, so **make cross-repo changes directly** —
when a protocol/stream change here needs a viewer change, edit the `crypt` viewer
in the same pass and keep BOTH `CLAUDE.md` files current. `docs/preview_protocol.md`
is the shared source of truth for the wire format (`CPV1`/`CPV2`, the control
plane, recording). *(The old one-way handoff changelog —
`docs/crypt_viewer_updates.md` here / `docs/capture_updates.md` in `crypt` — and
the seed `crypt_viewer_handoff.md` were removed 2026-07-06 once both repos became
directly editable; historical entries mentioning them are just prose.)* Both
repos develop on the same feature branch.

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
# Serve the browser port over wss:// (for a standalone headset on an https://
# viewer page — `npm run dev:https` in crypt; no adb needed):
#   openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem \
#       -days 365 -subj '/CN=<relay-ip>'
#   python3 -m central.preview_server --tls-cert cert.pem --tls-key key.pem
#   # viewer: https://<pc-ip>:5173/?ws=wss://<relay-ip>:8080  (accept both certs once)
# Discovery tests (query/reply encode + loopback round-trip):
python3 -m tests.test_discovery

# Live control (capture a background plate on all nodes without a browser):
python3 -m scripts.send_command --port 8080 capture-bg --frames 60
# Live camera controls (pick which Kinect data to send; stream adapts):
python3 -m scripts.send_command --port 8080 set-camera --align depth_to_color
# IR colour mode (points coloured from the active-IR image, same wire format):
python3 -m scripts.send_command --port 8080 set-ir --enabled on
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

# CPV2 compact wire format (uint16 quantised positions + valid-mask bitmap grid,
# ~52% smaller, quantum far below ToF noise). Default is cpv1; opt in once the
# crypt viewer ships the CPV2 decoder (docs/crypt_viewer_updates.md 2026-07-05):
python3 -m central.preview_server --wire cpv2
python3 -m tests.test_cpv2

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
- **WiFi power saving (2026-07-11)**: OFF on every node —
  `deploy/disable-wifi-powersave.sh` (run by `install-node-service.sh`; the
  unit also re-asserts `--runtime-only` at each start). Editing
  `NetworkManager.conf` + restarting NM does NOT work: Ubuntu ships
  `/etc/NetworkManager/conf.d/default-wifi-powersave-on.conf` (powersave=3),
  read AFTER the main file — later wins, so the edit is silently overridden;
  and the Orin devkit's Realtek RTL8822CE (rtw88) dozes on its own below
  nl80211 (deep LPS + PCIe ASPM). The script layers a `zz-*` NM drop-in
  (sorts last → wins), per-connection `powersave 2`, a dispatcher hook
  (`iw set power_save off` on every interface-up) and rtw88/iwlwifi modprobe
  options (need ONE reboot; generated only from parameters the loaded modules
  actually expose — an unknown option would make the module fail to load and
  kill WiFi outright). Existing nodes: pull +
  `sudo deploy/disable-wifi-powersave.sh` + reboot; verify
  `deploy/disable-wifi-powersave.sh --runtime-only` → "Power save: off"
  (the interface is often NOT wlan0 — predictable naming gives e.g.
  wlP1p1s0, hence `iw dev wlan0 …` → "No such device (-19)").
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

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
