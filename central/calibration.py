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
