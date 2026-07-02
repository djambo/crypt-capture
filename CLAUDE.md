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
            camera_modes.py (depth FOV / color res / fps / align tables, pyk4a-free),
            dump_calibration.py
central/    recorder.py (records synced takes), preview_server.py (live ws relay + control fan-out),
            calibration.py (rig extrinsics from a tracked marker ball: sphere fit + Kabsch)
processing/ mesh_take.py (take -> depth-grid PLY mesh)
scripts/    run_demo.py (hardware-free spine demo), preview_client.py (headless ws test),
            send_command.py (send control commands to the relay)
deploy/     kinect-node.service (+ .default env + install-node-service.sh):
            run the Jetson node as a boot-time, auto-restarting systemd service
tests/      test_rvl.py, test_background.py, test_camera.py, test_imu.py,
            test_extrinsic.py, test_discovery.py, test_calibration.py
docs/       hardware.md, protocol.md, preview_protocol.md, realtime_architecture.md,
            rig_calibration.md (marker-ball extrinsic calibration: procedure + wiring plan),
            crypt_viewer_handoff.md (initial CLAUDE.md for the `crypt` repo),
            crypt_viewer_updates.md (ongoing one-way change log for the viewer), jetson_setup.md
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
  need a re-run of the installer. `--headless`
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
4. **M3 — Record + download.** Trigger → node records full-rate to **local
   disk** → HTTP file server → recordings browser + download in UI.
5. **M4 — N nodes.** Node discovery/registry; trigger fans out to all.
6. 🟡 **Phase 2 (M5) — Aligned/fused.** ✅ *calibration method decided + math
   core built*: **marker-ball ("wand") calibration**, not ICP (inward-facing
   circle = no shared surface) and not boards (face ≤2 cameras) — a sphere's
   fitted center is the same 3D point for every camera, so waving a ball
   through the volume gives dense 3D↔3D correspondences → closed-form
   Kabsch per sensor. `central/calibration.py` (fit_sphere with known radius —
   cap-centroid alone is ~r/2 biased toward each camera; pair_tracks;
   solve_rigid; solve_rig) unit-tested synthetically to <0.01°/<1 mm
   (`tests/test_calibration.py`); full procedure + wiring plan in
   **`docs/rig_calibration.md`**. ⏳ remaining: `scripts/calibrate_rig.py`
   (collect + solve + write `rig_calib.json`), relay `--rig-calib` (apply
   `P_world = R_i·P + t_i` per sensor → one world frame on the wire, no viewer
   change), camera poses → viewer gizmos; then **TSDF fusion** (Open3D) →
   watertight mesh → glTF/meshopt export for the renderer. **Two-tier design**
   (see the doc): Tier-1 rough = zero-prop (IMU roll/pitch + floor height +
   body-centroid track for yaw/XY, ~5–10 cm, enough for scene editing);
   Tier-2 fine = the wand pass (~2–5 mm expected on real ToF). ChArUco was
   evaluated and rejected (board faces ≤2 cameras → chaining; weak Z; wrong
   modality — calibrate in the depth space you render). Retroreflective ball
   in IR = optional segmentation upgrade, same math.
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
