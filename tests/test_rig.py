"""Headless tests for the rig-calibration WIRING layer in central/calibration.py
(the math core itself is covered by tests/test_calibration.py):

  - BallTracker / CentroidTracker frame gating (person-in-frame, bad fits),
  - level_rotation + solve_yaw_translation + solve_rough (Tier-1 rough),
  - rig_calib.json round-trip,
  - the relay's apply step (P_world = R·P + t registers two synthetic views).

Run: python3 -m tests.test_rig
"""

import json
import os
import tempfile

import numpy as np

from central.calibration import (
    BallTracker, CentroidTracker, fit_sphere, level_rotation, load_rig_calib,
    rig_to_dict, save_rig_calib, solve_rig, solve_rough, solve_yaw_translation,
)

RNG = np.random.RandomState(11)
BALL_R = 0.05


def sphere_cap(center, radius, view_origin, n=300, noise=0.002):
    to_cam = view_origin - center
    to_cam = to_cam / np.linalg.norm(to_cam)
    pts = []
    while len(pts) < n:
        v = RNG.normal(size=3)
        v /= np.linalg.norm(v)
        if v.dot(to_cam) > 0.15:
            pts.append(center + v * radius)
    pts = np.array(pts)
    return pts + RNG.normal(scale=noise, size=pts.shape)


def rot_y(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rot_x(deg):
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def test_ball_tracker_gates():
    tr = BallTracker(BALL_R, min_points=40, max_points=8000, max_fit_rms=0.012)
    center = np.array([0.1, -0.2, -1.5])
    cap = sphere_cap(center, BALL_R, view_origin=np.zeros(3))
    assert tr.add(0, 0.0, cap) == "ok"
    # A person-sized blob: way too many points -> gated before fitting.
    person = RNG.uniform(-0.4, 0.4, size=(20000, 3))
    assert tr.add(0, 0.1, person) == "count"
    # Too few points to fit.
    assert tr.add(0, 0.2, cap[:10]) == "count"
    # Plausible count but nothing like a sphere -> fit residual rejects it.
    plane = np.column_stack([RNG.uniform(-0.3, 0.3, 500),
                             RNG.uniform(-0.3, 0.3, 500),
                             np.full(500, -1.5)])
    assert tr.add(0, 0.3, plane) == "fit"
    assert tr.counts() == {0: 1}
    assert tr.rejected[0] == {"count": 2, "fit": 1}
    c = tr.tracks[0][0][1]
    assert np.linalg.norm(c - center) < 0.005
    print("BallTracker gating: OK")


def test_centroid_tracker():
    tr = CentroidTracker(min_points=300)
    assert tr.add(1, 0.0, RNG.uniform(-1, 1, size=(50, 3))) == "count"
    body = RNG.uniform(-0.3, 0.3, size=(5000, 3)) + np.array([0, 0, -1.4])
    assert tr.add(1, 0.1, body) == "ok"
    assert np.linalg.norm(tr.tracks[1][0][1] - [0, 0, -1.4]) < 0.02
    print("CentroidTracker: OK")


def test_level_rotation():
    # A camera pitched 20 deg: gravity in its view frame is rot applied to
    # world down. level_rotation must take it back onto (0,-1,0).
    for R_tilt in (rot_x(20), rot_x(-35).dot(rot_y(50)), np.eye(3)):
        g_view = R_tilt.T.dot([0.0, -1.0, 0.0])
        L = level_rotation(g_view)
        assert np.allclose(L.dot(g_view), [0, -1, 0], atol=1e-9)
        assert np.allclose(L.dot(L.T), np.eye(3), atol=1e-9)  # proper rotation
        assert np.linalg.det(L) > 0.99
    # Degenerate + antipodal inputs don't blow up.
    assert np.allclose(level_rotation((0, 0, 0)), np.eye(3))
    up = level_rotation((0, 1, 0))
    assert np.allclose(up.dot([0, 1, 0]), [0, -1, 0], atol=1e-9)
    print("level_rotation: OK")


def test_solve_yaw_translation():
    R_true = rot_y(38.0)
    t_true = np.array([0.6, -0.15, 1.1])
    A = RNG.uniform(-1, 1, size=(80, 3))
    B = A.dot(R_true.T) + t_true + RNG.normal(scale=0.002, size=(80, 3))
    R, t, rms = solve_yaw_translation(A, B)
    assert np.linalg.norm(R - R_true) < 5e-3
    assert np.linalg.norm(t - t_true) < 3e-3
    assert rms < 5e-3
    print("solve_yaw_translation: OK (rms %.1f mm)" % (rms * 1000))


def test_solve_rough_end_to_end():
    """Two tilted+yawed cameras watching the same centroid track: solve_rough
    must recover each camera's pose into the leveled reference frame."""
    n = 90
    times = np.arange(n) / 30.0
    # Operator walks an "L" and raises an arm: a non-degenerate world track.
    world = np.stack([
        np.concatenate([np.linspace(0, 0.8, n // 2), np.full(n - n // 2, 0.8)]),
        1.0 + 0.15 * np.sin(times * 2.0),
        np.concatenate([np.zeros(n // 2), np.linspace(0, 0.9, n - n // 2)]),
    ], axis=1)

    # Sensor pose = view->world: world = T_i · view. Build the inverse to
    # generate each sensor's measurements: view = R_wv·world + t_wv.
    poses = {0: (rot_y(10).dot(rot_x(12)), np.array([0.0, 0.2, -2.0])),
             1: (rot_y(-120).dot(rot_x(-8)), np.array([1.5, -0.1, -1.2]))}
    tracks, gravities = {}, {}
    for sid, (R_vw, t_vw) in poses.items():
        R_wv = R_vw.T
        t_wv = -R_vw.T.dot(t_vw)
        tracks[sid] = [(times[k] + 0.001 * sid,
                        R_wv.dot(world[k]) + t_wv
                        + RNG.normal(scale=0.005, size=3))
                       for k in range(n)]
        # View-frame gravity = inverse pose applied to world down (IMU).
        gravities[sid] = R_wv.dot([0.0, -1.0, 0.0])

    rig = solve_rough(tracks, gravities, ref=0)
    assert set(rig) == {0, 1}
    # Expected world frame: ref's LEVELED frame = yaw-only leftover of pose 0.
    L0 = level_rotation(gravities[0])
    for sid in (0, 1):
        R_est = rig[sid]["R"]
        t_est = rig[sid]["t"]
        # Check on data: view points must map near the (leveled-frame) truth.
        R_vw, t_vw = poses[sid]
        for k in (0, n // 2, n - 1):
            v = np.asarray(tracks[sid][k][1])
            w_est = R_est.dot(v) + t_est
            # Truth in the leveled ref frame: level ref's view of the point.
            v0 = poses[0][0].T.dot(world[k]) - poses[0][0].T.dot(poses[0][1])
            w_true = L0.dot(v0)
            err = np.linalg.norm(w_est - w_true)
            assert err < 0.03, "sensor %d k=%d off %.1f mm" % (sid, k, err * 1e3)
        # The solved frame is level: mapped gravity must be world down.
        g_mapped = R_est.dot(gravities[sid])
        assert np.allclose(g_mapped, [0, -1, 0], atol=1e-6)
    print("solve_rough: OK (sensor 1 rms %.1f mm, %d pairs)"
          % (rig[1]["rms"] * 1000, rig[1]["pairs"]))


def test_rig_calib_roundtrip():
    sol = {0: {"R": np.eye(3), "t": np.zeros(3), "rms": 0.0, "pairs": 100},
           1: {"R": rot_y(25), "t": np.array([0.1, 0.2, 0.3]),
               "rms": 0.0021, "pairs": 88}}
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rig_calib.json")
        save_rig_calib(path, sol, tier="fine", ref=0, ball_radius=BALL_R)
        with open(path) as f:
            raw = json.load(f)
        assert raw["version"] == 1 and raw["tier"] == "fine"
        assert raw["ref"] == 0 and raw["ball_radius"] == BALL_R
        transforms, meta = load_rig_calib(path)
        assert set(transforms) == {0, 1}
        R1, t1 = transforms[1]
        assert R1.dtype == np.float32 and t1.dtype == np.float32
        assert np.allclose(R1, rot_y(25), atol=1e-6)
        assert np.allclose(t1, [0.1, 0.2, 0.3], atol=1e-6)
        assert meta["sensors"][1]["pairs"] == 88
        assert abs(meta["sensors"][1]["rms"] - 0.0021) < 1e-9
    print("rig_calib.json round-trip: OK")


def test_apply_registers_views():
    """The relay's apply step: two synthetic views of the same world points,
    each mapped by its solved (R,t), must land on top of each other."""
    world = RNG.uniform(-1, 1, size=(60, 3))
    times = np.arange(60) / 30.0
    poses = {0: (np.eye(3), np.zeros(3)),
             1: (rot_y(70).dot(rot_x(15)), np.array([1.2, 0.3, -0.4]))}
    tracks = {}
    for sid, (R_vw, t_vw) in poses.items():
        R_wv, t_wv = R_vw.T, -R_vw.T.dot(t_vw)
        tracks[sid] = [(times[k], R_wv.dot(world[k]) + t_wv)
                       for k in range(60)]
    rig = solve_rig(tracks, ref=0)
    # Apply exactly what the relay does: P_out = P @ R.T + t (float32).
    out = {}
    for sid in (0, 1):
        R = np.asarray(rig[sid]["R"], dtype=np.float32)
        t = np.asarray(rig[sid]["t"], dtype=np.float32)
        pts = np.array([p for _, p in tracks[sid]], dtype=np.float32)
        out[sid] = pts.dot(R.T) + t
    err = np.linalg.norm(out[0] - out[1], axis=1).max()
    assert err < 1e-4, "views misregistered by %.2f mm" % (err * 1000)
    print("apply step registers views: OK (max %.3f mm)" % (err * 1000))


if __name__ == "__main__":
    test_ball_tracker_gates()
    test_centroid_tracker()
    test_level_rotation()
    test_solve_yaw_translation()
    test_solve_rough_end_to_end()
    test_rig_calib_roundtrip()
    test_apply_registers_views()
    print("\nALL RIG-WIRING TESTS PASSED")
