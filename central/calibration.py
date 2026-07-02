"""
Rig extrinsic calibration from a tracked marker ball (the "wand" pass).

Why this method: the rig's sensors stand on a circle looking INWARD at the
subject, so any two cameras see mostly *different sides* of everything — ICP
has almost no shared surface to lock onto (and needs an initial guess anyway),
and a flat checkerboard/ArUco board can't face more than ~two cameras at once.
A small SPHERE has neither problem: it looks identical from every direction,
and although each camera only sees its facing cap, the fitted *center* is the
same physical 3D point for all of them. Waving the ball through the capture
volume for ~30 s therefore gives every camera a long, shared trajectory of
common 3D points — dense 3D↔3D correspondences — from which each camera's
rigid transform into a reference camera's frame is a closed-form solve
(Kabsch/Umeyama), no initial guess needed.

Pipeline (script wiring lives in scripts/calibrate_rig.py; this module is the
pure math, NumPy-only, unit-tested headlessly in tests/test_calibration.py):
  1. per sensor, per frame: foreground points of the ball -> fit_sphere()
     (known radius; the visible cap's centroid alone is biased toward each
     camera by ~r/2, which would poison the solve with a per-camera offset)
  2. pair each sensor's (time, center) track against the reference sensor's
     by nearest timestamp (pair_tracks) — hardware sync cables make this
     exact; without them a slowly-moved ball keeps pairing error small
  3. solve_rigid() per sensor -> R, t mapping that sensor's points into the
     reference frame; report RMS residual as the accuracy figure.

The transforms are applied at the RELAY (one canonical world frame on the
wire, per the north star), composed after unprojection; the viewer stays
source-agnostic and can place each sensor's gizmo from the same transforms.
"""

import json

import numpy as np


def fit_sphere(points, radius, iters=10):
    """Center of a sphere of KNOWN radius fitted to surface points (N,3).

    Gauss-Newton on residuals (|p - c| - r), initialised at the centroid pushed
    half a radius away from the origin (the camera): a camera only sees the
    facing cap, whose centroid sits ~r/2 in front of the true center, so this
    start point is already close and GN converges in a few iterations.
    Returns (center (3,), rms) or (None, None) if degenerate (< 4 points).
    """
    p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if p.shape[0] < 4:
        return None, None
    c = p.mean(axis=0)
    n = np.linalg.norm(c)
    if n > 1e-9:
        c = c * (1.0 + 0.5 * radius / n)     # push away from the camera
    for _ in range(iters):
        d = p - c                             # (N,3)
        dist = np.linalg.norm(d, axis=1)
        dist = np.maximum(dist, 1e-12)
        res = dist - radius                   # (N,)
        J = -d / dist[:, None]                # d res / d c
        g = J.T.dot(res)
        H = J.T.dot(J)
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            break
        c = c - step
        if np.linalg.norm(step) < 1e-7:
            break
    dist = np.linalg.norm(p - c, axis=1)
    rms = float(np.sqrt(np.mean((dist - radius) ** 2)))
    return c, rms


def solve_rigid(A, B):
    """Rigid transform (R, t) minimising |R·A + t - B|^2 (Kabsch/Umeyama).

    A, B: (N,3) corresponding points (N >= 3, not collinear). Returns
    (R (3,3), t (3,), rms). R is a proper rotation (det +1).
    """
    A = np.asarray(A, dtype=np.float64).reshape(-1, 3)
    B = np.asarray(B, dtype=np.float64).reshape(-1, 3)
    ca = A.mean(axis=0)
    cb = B.mean(axis=0)
    H = (A - ca).T.dot(B - cb)
    U, _, Vt = np.linalg.svd(H)
    S = np.eye(3)
    if np.linalg.det(Vt.T.dot(U.T)) < 0:      # reflection guard
        S[2, 2] = -1.0
    R = Vt.T.dot(S).dot(U.T)
    t = cb - R.dot(ca)
    res = (A.dot(R.T) + t) - B
    rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1))))
    return R, t, rms


def pair_tracks(track_a, track_b, max_dt=0.02):
    """Pair two (time, point) tracks by nearest timestamp.

    track_*: sequences of (t_seconds, center (3,)). Returns (A, B) arrays of
    matched points (one match per track_a sample at most, within max_dt).
    Hardware-synced sensors pair exactly; free-running ones rely on a slowly
    moved ball (at 0.5 m/s, 16 ms of skew = 8 mm — folded into the residual).
    """
    if not track_a or not track_b:
        return np.zeros((0, 3)), np.zeros((0, 3))
    tb = np.array([s[0] for s in track_b])
    order = np.argsort(tb)
    tb = tb[order]
    pb = np.array([track_b[i][1] for i in order])
    A, B = [], []
    for ta, pa in track_a:
        i = int(np.searchsorted(tb, ta))
        best, bdt = None, max_dt
        for j in (i - 1, i):
            if 0 <= j < len(tb):
                dt = abs(tb[j] - ta)
                if dt <= bdt:
                    best, bdt = j, dt
        if best is not None:
            A.append(pa)
            B.append(pb[best])
    return np.asarray(A, dtype=np.float64), np.asarray(B, dtype=np.float64)


def solve_rig(tracks, ref=None, max_dt=0.02, min_pairs=30):
    """Solve every sensor's rigid transform into a reference sensor's frame.

    tracks: {sensor_id: [(t_seconds, center (3,)), ...]} — the wand pass.
    ref: reference sensor id (default: lowest id present).
    Returns {sensor_id: {"R": (3,3), "t": (3,), "rms": float, "pairs": int}},
    with the reference mapping to identity. Sensors with fewer than min_pairs
    matched samples are omitted (not enough shared trajectory).
    """
    if not tracks:
        return {}
    if ref is None:
        ref = min(tracks)
    out = {ref: {"R": np.eye(3), "t": np.zeros(3), "rms": 0.0,
                 "pairs": len(tracks[ref])}}
    for sid, track in tracks.items():
        if sid == ref:
            continue
        A, B = pair_tracks(track, tracks[ref], max_dt=max_dt)
        if A.shape[0] < min_pairs:
            continue
        R, t, rms = solve_rigid(A, B)
        out[sid] = {"R": R, "t": t, "rms": rms, "pairs": int(A.shape[0])}
    return out


# --------------------------------------------------------------------------
# Frame-level collection (shared by scripts/calibrate_rig.py and the relay's
# viewer-driven calibration sessions). Frames come in as the raw per-sensor
# point clouds the relay unprojects (view frame, BEFORE any rig transform);
# the trackers gate out implausible frames and accumulate (time, point) tracks
# in the shape solve_rig()/solve_rough() consume.
# --------------------------------------------------------------------------

class BallTracker:
    """Accumulates per-sensor (time, ball-center) tracks for the Tier-2 wand
    pass. Each frame is gated before fitting:

      - point count must be plausible for the ball alone (a person in frame is
        tens of thousands of points; the ball's visible cap is tens..thousands
        depending on distance) — reject 'count';
      - the sphere fit must converge with a small residual (ToF noise is a few
        mm; a body part masquerading as foreground fits terribly) — reject 'fit'.

    Gates are deliberately loose defaults, tunable from the CLI/command.
    """

    def __init__(self, radius, min_points=40, max_points=8000,
                 max_fit_rms=0.012):
        self.radius = float(radius)
        self.min_points = int(min_points)
        self.max_points = int(max_points)
        self.max_fit_rms = float(max_fit_rms)
        self.tracks = {}            # sensor_id -> [(t_seconds, center (3,))]
        self.rejected = {}          # sensor_id -> {"count": n, "fit": n}

    def _reject(self, sensor_id, reason):
        r = self.rejected.setdefault(sensor_id, {"count": 0, "fit": 0})
        r[reason] += 1
        return reason

    def add(self, sensor_id, t_seconds, points):
        """Consider one frame. Returns 'ok', 'count' or 'fit'."""
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if p.shape[0] < self.min_points or p.shape[0] > self.max_points:
            return self._reject(sensor_id, "count")
        c, rms = fit_sphere(p, self.radius)
        if c is None or rms > self.max_fit_rms:
            return self._reject(sensor_id, "fit")
        self.tracks.setdefault(sensor_id, []).append((float(t_seconds), c))
        return "ok"

    def counts(self):
        return {sid: len(track) for sid, track in self.tracks.items()}


class CentroidTracker:
    """Accumulates per-sensor (time, foreground-centroid) tracks for the Tier-1
    rough pass. The landmark is the operator's body (after background
    subtraction), so the only gate is 'enough points to be a person'."""

    def __init__(self, min_points=300):
        self.min_points = int(min_points)
        self.tracks = {}            # sensor_id -> [(t_seconds, centroid (3,))]

    def add(self, sensor_id, t_seconds, points):
        p = np.asarray(points, dtype=np.float64).reshape(-1, 3)
        if p.shape[0] < self.min_points:
            return "count"
        self.tracks.setdefault(sensor_id, []).append(
            (float(t_seconds), p.mean(axis=0)))
        return "ok"

    def counts(self):
        return {sid: len(track) for sid, track in self.tracks.items()}


# --------------------------------------------------------------------------
# Tier-1 rough solve: per-camera IMU roll/pitch (leveling) + body-centroid
# track match for yaw/XY (+ relative height). See docs/rig_calibration.md.
# --------------------------------------------------------------------------

def level_rotation(gravity_view):
    """Rotation taking a measured view-frame gravity (down) unit vector onto
    world down (0,-1,0) — the roll/pitch part of a sensor's pose, straight from
    its IMU. Returns identity for a degenerate input."""
    g = np.asarray(gravity_view, dtype=np.float64).reshape(3)
    n = np.linalg.norm(g)
    if n < 1e-9:
        return np.eye(3)
    g = g / n
    d = np.array([0.0, -1.0, 0.0])
    v = np.cross(g, d)
    c = float(g.dot(d))
    if c < -1.0 + 1e-9:                  # g points straight UP: 180° about X
        return np.diag([1.0, -1.0, -1.0])
    vx = np.array([[0.0, -v[2], v[1]],
                   [v[2], 0.0, -v[0]],
                   [-v[1], v[0], 0.0]])
    return np.eye(3) + vx + vx.dot(vx) * (1.0 / (1.0 + c))


def solve_yaw_translation(A, B):
    """Best yaw (rotation about +Y) + translation mapping A onto B (N,3 each,
    already leveled). Restricting the rotation to yaw is what makes centroid
    tracks usable: the centroid is biased toward each camera by roughly half
    the body depth, and a full 3D Kabsch would convert that bias into a bogus
    tilt — leveling comes from the IMU instead, which measures it directly.
    Returns (R (3,3), t (3,), rms)."""
    A = np.asarray(A, dtype=np.float64).reshape(-1, 3)
    B = np.asarray(B, dtype=np.float64).reshape(-1, 3)
    ca = A.mean(axis=0)
    cb = B.mean(axis=0)
    a = A - ca
    b = B - cb
    # R_y(phi): x' = c·x + s·z ; z' = -s·x + c·z. Maximise sum(b · R a).
    C = float(np.sum(a[:, 0] * b[:, 0] + a[:, 2] * b[:, 2]))
    S = float(np.sum(a[:, 2] * b[:, 0] - a[:, 0] * b[:, 2]))
    phi = np.arctan2(S, C)
    c, s = np.cos(phi), np.sin(phi)
    R = np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])
    t = cb - R.dot(ca)
    res = A.dot(R.T) + t - B
    rms = float(np.sqrt(np.mean(np.sum(res ** 2, axis=1))))
    return R, t, rms


def solve_rough(tracks, gravities, ref=None, max_dt=0.05, min_pairs=15):
    """Tier-1 rough rig solve (zero props, ~5-10 cm expected).

    tracks: {sensor_id: [(t_seconds, centroid (3,)), ...]} — the operator's
        foreground-centroid track per sensor (walk a small "L").
    gravities: {sensor_id: (gx,gy,gz)} — view-frame gravity per sensor (IMU).

    Per sensor: level by its own gravity (roll/pitch), then solve yaw + XYZ
    translation against the reference sensor's leveled track. The world frame
    is the REFERENCE sensor's LEVELED frame, so the floor comes out flat by
    construction (the reference maps by its own leveling, not identity).
    Returns the same shape as solve_rig(); sensors with too few matched pairs
    are omitted.
    """
    if not tracks:
        return {}
    if ref is None:
        ref = min(tracks)
    levels = {sid: level_rotation(gravities.get(sid, (0.0, -1.0, 0.0)))
              for sid in tracks}
    leveled = {sid: [(t, levels[sid].dot(c)) for t, c in track]
               for sid, track in tracks.items()}
    out = {ref: {"R": levels[ref], "t": np.zeros(3), "rms": 0.0,
                 "pairs": len(tracks[ref])}}
    for sid, track in leveled.items():
        if sid == ref:
            continue
        A, B = pair_tracks(track, leveled[ref], max_dt=max_dt)
        if A.shape[0] < min_pairs:
            continue
        R_yaw, t, rms = solve_yaw_translation(A, B)
        out[sid] = {"R": R_yaw.dot(levels[sid]), "t": t, "rms": rms,
                    "pairs": int(A.shape[0])}
    return out


# --------------------------------------------------------------------------
# rig_calib.json I/O — the file the calibration writes and the relay applies.
# Per sensor: R (3x3, row-major nested lists), t (metres), rms, pairs; plus
# tier ("fine" | "rough"), reference sensor id and the ball radius used.
# --------------------------------------------------------------------------

def rig_to_dict(solution, tier, ref, ball_radius=None):
    sensors = {}
    for sid, s in solution.items():
        sensors[str(int(sid))] = {
            "R": np.asarray(s["R"], dtype=float).reshape(3, 3).tolist(),
            "t": np.asarray(s["t"], dtype=float).reshape(3).tolist(),
            "rms": float(s["rms"]),
            "pairs": int(s["pairs"]),
        }
    out = {"version": 1, "tier": tier, "ref": int(ref), "sensors": sensors}
    if ball_radius is not None:
        out["ball_radius"] = float(ball_radius)
    return out


def save_rig_calib(path, solution, tier, ref, ball_radius=None):
    with open(path, "w") as f:
        json.dump(rig_to_dict(solution, tier, ref, ball_radius), f, indent=2)
        f.write("\n")


def load_rig_calib(path):
    """Load rig_calib.json -> ({sensor_id: (R (3,3) f32, t (3,) f32)}, meta).
    meta echoes tier/ref/ball_radius plus per-sensor rms/pairs for display."""
    with open(path) as f:
        data = json.load(f)
    transforms = {}
    meta = {"tier": data.get("tier"), "ref": data.get("ref"),
            "ball_radius": data.get("ball_radius"), "sensors": {}}
    for sid_str, s in data.get("sensors", {}).items():
        sid = int(sid_str)
        R = np.asarray(s["R"], dtype=np.float32).reshape(3, 3)
        t = np.asarray(s["t"], dtype=np.float32).reshape(3)
        transforms[sid] = (R, t)
        meta["sensors"][sid] = {"rms": float(s.get("rms", 0.0)),
                                "pairs": int(s.get("pairs", 0))}
    return transforms, meta
