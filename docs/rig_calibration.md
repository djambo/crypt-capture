# Rig extrinsic calibration — the marker-ball ("wand") procedure

Goal: solve, for every sensor `i`, the rigid transform `T_i = [R|t]` that maps
its points into **one shared metric world frame**, so N inward-facing Kinects
render as one registered scene. This is the M5 milestone's first half (the
seam-dissolving TSDF fusion builds on top of it).

## Two tiers of alignment (design decision)

Alignment serves two different needs, with different accuracy/effort budgets:

1. **Tier 1 — rough, zero props, seconds** (good enough to start scene editing
   in the web app; ~5–10 cm / a few degrees). Composed from what the pipeline
   already measures, per camera:
   - **IMU gravity** → roll + pitch (already streams; the viewer levels today);
   - **floor-plane detection** → height (already built, `FloorDetector`);
   - **the operator's body as the landmark** → yaw + XY: each camera sees the
     person; match the foreground **centroid track** across cameras (stand
     centre, raise an arm / walk a small "L" so the track isn't a point). No
     props, fully automatic — the "walk in and it lines up" experience.
2. **Tier 2 — fine, the marker-ball wand pass, ~mm** (recording / VR /
   fusion-ready). The rest of this doc. Optional later stack-ons for the last
   millimetre: pair-wise **ICP refinement on overlapping static geometry**
   (floor/walls adjacent cameras share) and a **joint bundle solve** — they
   *refine* the wand result, never replace it.

**Why the body can't be Tier 2:** the body is non-rigid and each camera sees a
*different side* of it — there is no observable "same physical point" across
opposing views, so body-based alignment caps out around centimetres no matter
how good the detector is. Centroids are for rough; a view-invariant landmark
(a sphere's centre) is what reaches millimetres.

## Accuracy: sphere (depth) vs ChArUco (color) vs IR — the honest math

- **Sphere/depth:** ToF noise ~1–2 mm random + a few mm systematic per camera.
  A cap fit over hundreds of points averages the random part to ~0.3 mm; the
  rigid solve over a 30 s volume-filling trajectory averages further, and
  rotation error ≈ residual ÷ trajectory extent (~3 mm / 1.5 m ≈ 0.1°).
  **Expected end-to-end: ~2–5 mm** (synthetic floor <1 mm; real ToF bias sets
  the ceiling).
- **ChArUco/color:** subpixel corners are sharper *laterally* (~0.3 mm at 2 m
  at 4K), but (a) pose-from-a-flat-board is weakly conditioned along Z/tilt
  (~5–15 mm per shot at 2 m for a 40 cm board), (b) an inward-facing circle
  forces **pair chaining** (a board faces ≤2 cameras) → errors compound to
  **~1–2 cm cross-rig** without a bundle solve, and (c) — decisive — it
  minimises error in *color-image space* while the thing that must register is
  the *depth clouds*: per-camera depth↔color systematic offsets can leave the
  rendered clouds ~1 cm apart with no knob to fix. **Calibrate in the modality
  you render.**
- **IR:** not a competing method — the depth cam doubles as an IR camera, and a
  **retroreflective ball** glows in active IR, making detection trivial. It's a
  *segmentation upgrade* to the same sphere math if background-subtraction
  detection ever proves flaky. Same accuracy after detection.

**Verdict:** the sphere wins for this rig — per-pair accuracy on par or better,
no chaining, all cameras solved simultaneously against the same trajectory, and
matched modality (residuals measured in exactly the space we render).

## Why a ball, not ICP or a board

- **ICP is out** (user-confirmed intuition): the sensors stand on a circle
  looking inward, so two cameras see mostly *different sides* of the subject —
  there is almost no shared surface to converge on, and ICP needs a good
  initial transform anyway. (It remains useful later as a *fine refinement*
  between adjacent, already-roughly-registered views.)
- **Flat boards (checker/ArUco) are awkward**: a board faces at most ~2 cameras
  at once, so a 4-camera circle needs board repositioning + transform chaining
  (0→1→2→3), accumulating error, plus OpenCV and the color intrinsics path.
- **A sphere looks the same from everywhere.** Every camera sees its facing
  cap, but the fitted *center* is the same physical point for all of them.
  Wave the ball through the volume and every camera records the same 3D
  trajectory in its own frame — dense 3D↔3D correspondences, solved in closed
  form (Kabsch/Umeyama), no initial guess, no chaining, all cameras at once —
  and it reuses the existing depth/foreground pipeline end to end.

## The rough (Tier-1) procedure — operator's view

Zero props, ~10 s, good to ~5–10 cm. The landmark is **your own body's
foreground centroid**, so what matters is that every camera sees the same
person tracing a path with real spatial extent:

1. All nodes streaming. **Capture background** on every sensor with the scene
   empty. (The IMU orientation toggle is NOT needed — each node already sent
   its gravity vector at connect, and the solve uses that for roll/pitch.)
2. **ONE person** walks in — you must be the only foreground on every camera.
3. Press **Rough Align** (or `send_command calibrate-rough`), then for the
   ~10 s window:
   - **walk a slow "L"** — roughly 2 m in one direction, turn, 2 m
     perpendicular — through the **middle** of the capture volume. Two legs in
     different directions are what pin down yaw + XY; a straight line or
     standing still leaves the solve degenerate.
   - **raise an arm** for part of it (vertical variation helps the height
     match), and move at a normal walking pace or slower.
   - stay **fully visible to every camera** the whole time — if you leave one
     camera's view its track has holes and it may come back "unsolved."
4. Watch the status line: the per-camera sample counters should all climb at
   roughly the same rate. On "done" the clouds snap to within ~5–10 cm —
   enough for scene editing; run the wand pass (below) before recording.
5. Not right? Press **Reset** (clears the calibration, cancels a running pass,
   back to raw camera frames) and go again.

Accuracy honesty: the centroid each camera sees is biased toward that camera
by ~half your body depth, which is exactly why this tier stops at centimetres
and why the solver only takes yaw/XY from it (roll/pitch come from the IMU).

## The fine (Tier-2) wand procedure — operator's view

1. All nodes streaming, empty scene. **Capture background** on every sensor.
2. Walk in holding the **calibration ball** (a plain rigid sphere, ~10–20 cm —
   e.g. a styrofoam ball on a short stick; matte, not black). The ball + you
   are now the only foreground.
   *(v1 assumption: the ball is held out on the stick away from the body so
   per-frame foreground clusters cleanly; a later pass can add "largest
   spherical cluster" segmentation to relax this.)*
3. **Wave the ball slowly through the whole capture volume** for ~30 s —
   cover left/right, up/down, near/far; slow beats fast (motion skew).
4. Run the calibration script (below). It reports per-sensor RMS residual —
   **millimetres = good; centimetres = re-run** (ball too fast, or a sensor
   barely saw it).
5. Transforms are saved to `rig_calib.json` and applied by the relay from then
   on. Re-run only when cameras physically move. A bad pass is undone with the
   viewer's **Reset** button (`clear_rig_calib`) — it also cancels a
   still-running collection.

## The math (implemented + unit-tested)

`central/calibration.py` (NumPy-only; `python3 -m tests.test_calibration`):

- `fit_sphere(points, radius)` — Gauss-Newton center fit with **known radius**.
  The visible cap's centroid alone is biased toward each camera by ~r/2
  (measured in the test: 30 mm bias for a 10 cm ball vs 0.3 mm fitted), which
  would poison the solve with per-camera offsets — that's why we fit.
- `pair_tracks(a, b, max_dt)` — nearest-timestamp correspondence pairing.
  Hardware sync (3.5 mm cables) makes this exact; free-running works if the
  ball moves slowly (0.5 m/s × 16 ms skew = 8 mm, folded into the residual).
- `solve_rigid(A, B)` — Kabsch/Umeyama closed-form `R, t` + RMS.
- `solve_rig(tracks, ref)` — everything per sensor into the reference frame.
  Synthetic end-to-end (3 sensors on a circle, noisy caps, dropped frames,
  clock skew): rotation recovered to <0.01°, translation to <1 mm.

## Wiring (BUILT 2026-07-02 — how it actually works)

1. ✅ **`scripts/calibrate_rig.py`** (central): connects to the relay WebSocket
   like `scripts/preview_client.py`, collects per-sensor `CPV1` frames for
   `--seconds 30`, runs `fit_sphere` per frame (`--ball-radius`, default
   0.05 m), `solve_rig`, prints residuals, writes `rig_calib.json`
   (per-sensor R 3×3 row-major, t metres, rms, pairs; + `tier`/`ref`/
   `ball_radius`). Frames implausible for the ball are gated (count bounds +
   sphere-fit rms; `--min-points/--max-points/--max-fit-rms`); the frame-level
   collection lives in `central/calibration.BallTracker` (shared with the
   relay sessions below). Correspondence time = client arrival time (hardware
   sync + a slowly-waved ball keep the pairing error in the residual).
2. ✅ **Relay applies the calib** (`central/preview_server.py --rig-calib`,
   default `rig_calib.json`, absent file = exact no-op): after unprojection
   (view space), each sensor's points get `P_world = R_i·P + t_i` (and its
   gravity vector is rotated with them). One canonical world frame **on the
   wire** — the viewer needs no change to see registered clouds, and
   recording/fusion inherit the same frame (live == recorded, per the north
   star). The file is **mtime-watched** (re-running the script re-registers
   live) and re-readable via a `reload_rig_calib` command.
3. ✅ **Camera poses to the viewer** (gizmos): the relay sends a
   `{"type":"rig_poses"}` JSON text message on client connect and on every
   calib (re)load/clear, listing per-sensor `[R|t]` + rms/pairs; the viewer
   places each sensor's `CameraGizmo` at its true pose (empty = back to the
   origin). Spec: `docs/preview_protocol.md` §downstream.
4. ✅ **Viewer-driven runs + Tier-1 rough**: the viewer's `Fine Align (wand)` /
   `Rough Align` buttons send `calibrate_fine {seconds, ball_radius}` /
   `calibrate_rough {seconds}`; the RELAY collects (off the raw pre-transform
   clouds, so re-runs are correct), broadcasts `calib_status` progress ~1 Hz,
   solves, writes `rig_calib.json` and applies it. Rough =
   `central/calibration.solve_rough`: per-sensor IMU leveling
   (`level_rotation`) + a yaw-only Kabsch (`solve_yaw_translation`) on the
   body-centroid tracks — yaw-only because the centroid is biased toward each
   camera (~half the body depth) and a full 3D solve would turn that bias into
   a bogus tilt; roll/pitch come from the IMU instead. The rough world frame
   is the reference sensor's LEVELED frame (floor flat by construction).
5. Later polish: per-pair ICP fine refinement on overlapping static
   environment; joint (bundle) solve over all pairs instead of star-to-ref;
   auto ball segmentation (largest spherical cluster / retroreflective ball in
   IR) so the operator's body can be in frame.

**Headless verification** (no hardware): `node/sim_node.py --ball 0.05 --pose
"yaw_deg,x,y,z"` ray-renders a shared wall-clock-driven sphere from a known
pose. Two posed sim nodes + the relay: `calibrate_rig.py` recovered a
50°/1.2 m ground-truth pose to **0.16° / 3 mm**; the on-the-wire clouds then
register (paired ball centers coincide); the viewer flow (buttons → status →
gizmo poses) verified in headless Chromium. Unit tests:
`python3 -m tests.test_rig` (trackers/gates, rough solve, JSON round-trip,
apply step) alongside the original `tests/test_calibration.py` math tests.

## Related future work (noted here so the design accounts for it)

**Hand positions as particle attractors (Phase 3):** does NOT need the Kinect
Body Tracking SDK (x86-only, not on Jetson). Modern open-source 2D pose/hands
(MediaPipe Pose/Hands, RTMPose, MoveNet) runs realtime on the Orin against the
node's color image, which is already pixel-aligned with depth → keypoint (u,v)
→ depth lookup → true 3D hand positions, shipped as a tiny per-frame metadata
message and fed to the viewer's particle fields as attractors. Rig calibration
makes those hand positions valid in the shared world frame automatically.

## Status

- ✅ Math core + headless tests (`central/calibration.py`,
  `tests/test_calibration.py`).
- ✅ Viewer: per-sensor tinted gizmos + per-sensor IMU down sticks (crypt).
- ✅ **Wiring steps 1–4 (2026-07-02)**: `scripts/calibrate_rig.py`, relay
  `--rig-calib` apply + mtime watch, `rig_poses`/`calib_status` JSON to the
  viewer, viewer-driven `calibrate_fine`/`calibrate_rough` sessions, Tier-1
  rough solve (`solve_rough`), sim-node ball/pose test mode, `tests/test_rig.py`.
  Viewer side (crypt) wired the same day: live Align buttons + ball-radius
  input + posed gizmos. Verified headless end-to-end (see above).
- ⏳ Remaining: the real-hardware wand pass (2 Jetsons; set the true ball
  radius); then TSDF fusion on the registered frames (step 5 polish as needed).
