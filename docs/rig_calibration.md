# Rig extrinsic calibration — the marker-ball ("wand") procedure

Goal: solve, for every sensor `i`, the rigid transform `T_i = [R|t]` that maps
its points into **one shared metric world frame**, so N inward-facing Kinects
render as one registered scene. This is the M5 milestone's first half (the
seam-dissolving TSDF fusion builds on top of it).

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

## The procedure (operator's view)

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
   on. Re-run only when cameras physically move.

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

## Wiring plan (next steps, in order)

1. **`scripts/calibrate_rig.py`** (central): connects to the relay WebSocket
   like `scripts/preview_client.py`, collects per-sensor `CPV1` frames for
   `--seconds 30`, runs `fit_sphere` per frame (`--ball-radius`, default
   0.05 m), `solve_rig`, prints residuals, writes `rig_calib.json`
   (per-sensor R 3×3 row-major, t metres, rms, pairs). Gate: skip frames
   whose foreground count is implausible for the ball (person still in frame).
2. **Relay applies the calib** (`central/preview_server.py --rig-calib
   rig_calib.json`): after unprojection (view space), each sensor's points get
   `P_world = R_i·P + t_i`. One canonical world frame **on the wire** — the
   viewer needs no change to see registered clouds, and recording/fusion
   inherit the same frame (live == recorded, per the north star).
3. **Camera poses to the viewer** (gizmos): the relay sends a small JSON text
   message on client connect (and on calib reload) listing per-sensor `[R|t]`;
   the viewer places each sensor's `CameraGizmo` at its true pose. (The viewer
   already renders one tinted gizmo per sensor, currently all at the origin.)
4. Later polish: per-pair ICP fine refinement on overlapping static
   environment; joint (bundle) solve over all pairs instead of star-to-ref;
   auto ball segmentation (largest spherical cluster) so the operator's body
   can be in frame.

## Status

- ✅ Math core + headless tests (`central/calibration.py`,
  `tests/test_calibration.py`).
- ✅ Viewer: per-sensor tinted gizmos + per-sensor IMU down sticks (crypt).
- ⏳ Steps 1–3 above (script, relay flag, pose message).
