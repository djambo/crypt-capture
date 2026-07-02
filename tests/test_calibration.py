"""Headless tests for central/calibration.py (the wand-calibration math).

Synthetic ground truth throughout: random rigid transforms per sensor, a smooth
random ball trajectory, camera-facing sphere caps with depth-like noise. The
solver must recover each transform to millimetres/fractions of a degree.
Run: python3 -m tests.test_calibration
"""

import numpy as np

from central.calibration import fit_sphere, pair_tracks, solve_rigid, solve_rig

RNG = np.random.RandomState(7)
BALL_R = 0.05  # 10 cm ball


def random_rotation():
    q = RNG.normal(size=4)
    q /= np.linalg.norm(q)
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def sphere_cap(center, radius, view_origin, n=400, noise=0.002):
    """Points on the sphere cap FACING view_origin, +/- depth-ish noise."""
    to_cam = view_origin - center
    to_cam = to_cam / np.linalg.norm(to_cam)
    pts = []
    while len(pts) < n:
        v = RNG.normal(size=3)
        v /= np.linalg.norm(v)
        if v.dot(to_cam) > 0.15:              # visible hemisphere-ish
            pts.append(center + v * radius)
    pts = np.array(pts)
    return pts + RNG.normal(scale=noise, size=pts.shape)


def trajectory(n=120):
    """A smooth wandering path through a ~1.5 m capture volume."""
    t = np.linspace(0, 6 * np.pi, n)
    return np.stack([
        0.6 * np.sin(t * 0.7) + 0.1 * np.sin(t * 2.3),
        0.4 * np.sin(t * 1.1) + 1.0,
        0.6 * np.cos(t * 0.9) + 0.1 * np.cos(t * 3.1),
    ], axis=1)


def test_fit_sphere():
    center = np.array([0.2, -0.1, 1.8])
    pts = sphere_cap(center, BALL_R, view_origin=np.zeros(3))
    c, rms = fit_sphere(pts, BALL_R)
    err = np.linalg.norm(c - center)
    assert err < 0.004, "sphere center off by %.4f m" % err
    assert rms < 0.01
    # Degenerate input is refused, not crashed on.
    c2, _ = fit_sphere(pts[:3], BALL_R)
    assert c2 is None
    # Centroid alone would be biased toward the camera by ~r/2 — confirm the
    # fit beats it decisively (this is WHY we fit instead of averaging).
    centroid_err = np.linalg.norm(pts.mean(axis=0) - center)
    assert centroid_err > 3 * err
    print("fit_sphere: OK (err %.1f mm, centroid bias %.1f mm)"
          % (err * 1000, centroid_err * 1000))


def test_solve_rigid():
    R_true = random_rotation()
    t_true = np.array([0.4, -0.2, 2.5])
    A = RNG.uniform(-1, 1, size=(200, 3))
    B = A.dot(R_true.T) + t_true + RNG.normal(scale=0.001, size=(200, 3))
    R, t, rms = solve_rigid(A, B)
    assert np.linalg.norm(R - R_true) < 1e-2
    assert np.linalg.norm(t - t_true) < 2e-3
    assert rms < 3e-3
    print("solve_rigid: OK (rms %.1f mm)" % (rms * 1000))


def test_pair_tracks():
    pts = trajectory(50)
    ta = [(i / 30.0, pts[i]) for i in range(50)]
    tb = [(i / 30.0 + 0.004, pts[i] + 0.001) for i in range(0, 50, 2)]
    A, B = pair_tracks(ta, tb, max_dt=0.02)
    assert A.shape[0] >= 24                     # every other sample matched
    A2, B2 = pair_tracks(ta, [(9.0, np.zeros(3))], max_dt=0.02)
    assert A2.shape[0] == 0                     # nothing within max_dt
    print("pair_tracks: OK (%d pairs)" % A.shape[0])


def test_solve_rig_end_to_end():
    """Full synthetic wand pass: 3 sensors on a circle looking inward."""
    world_path = trajectory(150)                # ball centers, world frame
    times = np.arange(150) / 30.0
    # Sensor poses: on a 2.2 m circle, looking roughly at the centre.
    sensor_T = {}                               # world -> sensor_i
    for sid, ang in ((0, 0.0), (1, 2.1), (2, 4.2)):
        R = random_rotation()
        t = np.array([2.2 * np.cos(ang), RNG.uniform(-0.2, 0.2),
                      2.2 * np.sin(ang)])
        sensor_T[sid] = (R, t)

    tracks = {}
    for sid, (R, t) in sensor_T.items():
        cam_origin_world = -R.T.dot(t)          # camera position in world
        track = []
        for k in range(150):
            if sid == 2 and k % 3 == 0:
                continue                        # sensor 2 drops frames
            center_w = world_path[k]
            center_s = R.dot(center_w) + t      # ball center in sensor frame
            cap_w = sphere_cap(center_w, BALL_R,
                               view_origin=cam_origin_world, n=150)
            cap_s = cap_w.dot(R.T) + t
            c, _ = fit_sphere(cap_s, BALL_R)
            jitter = 0.001 * (sid + 1)          # per-sensor clock skew
            track.append((times[k] + jitter, c))
            del center_s
        tracks[sid] = track

    rig = solve_rig(tracks, ref=0)
    assert set(rig) == {0, 1, 2}
    for sid in (1, 2):
        R_est, t_est = rig[sid]["R"], rig[sid]["t"]
        # Ground truth sensor_i -> sensor_0: x0 = R01·xi + t01 with
        # R01 = R0·Ri^T, t01 = t0 - R01·ti.
        R0, t0 = sensor_T[0]
        Ri, ti = sensor_T[sid]
        R_true = R0.dot(Ri.T)
        t_true = t0 - R_true.dot(ti)
        rot_err = np.degrees(np.arccos(
            np.clip((np.trace(R_est.T.dot(R_true)) - 1) / 2, -1, 1)))
        t_err = np.linalg.norm(t_est - t_true)
        assert rot_err < 0.5, "sensor %d rotation off %.2f deg" % (sid, rot_err)
        assert t_err < 0.01, "sensor %d translation off %.1f mm" % (sid, t_err * 1000)
        print("solve_rig sensor %d: OK (rot %.3f deg, t %.1f mm, rms %.1f mm, "
              "%d pairs)" % (sid, rot_err, t_err * 1000,
                             rig[sid]["rms"] * 1000, rig[sid]["pairs"]))


if __name__ == "__main__":
    test_fit_sphere()
    test_solve_rigid()
    test_pair_tracks()
    test_solve_rig_end_to_end()
    print("\nALL CALIBRATION TESTS PASSED")
