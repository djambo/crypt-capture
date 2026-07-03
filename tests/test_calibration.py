"""Headless tests for central/calibration.py (the wand-calibration math).

Synthetic ground truth throughout: random rigid transforms per sensor, a smooth
random ball trajectory, camera-facing sphere caps with depth-like noise. The
solver must recover each transform to millimetres/fractions of a degree.
Run: python3 -m tests.test_calibration
"""

import numpy as np

from central.calibration import (fit_sphere, pair_tracks, solve_rigid,
                                 solve_rig, segment_ball,
                                 StationaryBallSampler)

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


def test_segment_ball():
    # The realistic wand-pass frame: the operator's BODY plus the ball, both in
    # the (background-subtracted) foreground. The old whole-cloud fit died on
    # this — the body's point count blew the gate. segment_ball must pull the
    # ball cluster out and ignore the body.
    center = np.array([0.35, 1.1, 1.9])
    ball = sphere_cap(center, BALL_R, view_origin=np.zeros(3), n=500)
    # A big blobby "body": a tall box of points well away from the ball so the
    # thin-stick gap is represented (no bridging points between them).
    body = np.column_stack([
        RNG.uniform(-0.25, 0.25, 9000),
        RNG.uniform(0.0, 1.7, 9000),
        RNG.uniform(2.3, 2.6, 9000),
    ])
    cloud = np.vstack([ball, body])
    RNG.shuffle(cloud)
    c, rms, n = segment_ball(cloud, BALL_R)
    assert c is not None, "ball not found in a body+ball foreground"
    err = np.linalg.norm(c - center)
    assert err < 0.006, "segmented ball center off by %.4f m" % err
    assert n >= 200 and n <= 900, "picked the wrong cluster (%d pts)" % n
    # A cloud that is ONLY the body has no ball -> honest miss, not a false lock.
    assert segment_ball(body, BALL_R)[0] is None
    # An elongated, ball-SIZED leg/arm fragment (a short cylinder shell) must be
    # rejected by the sphericity gate — this is the "lock jumps onto my leg" bug.
    axis = np.linspace(-0.18, 0.18, 600)              # ~36 cm long
    ang = RNG.uniform(0, 2 * np.pi, 600)
    leg = np.column_stack([
        BALL_R * np.cos(ang) + 0.9,
        axis + 1.0,                                    # long axis = vertical
        BALL_R * np.sin(ang) + 2.0,
    ]) + RNG.normal(scale=0.002, size=(600, 3))
    assert segment_ball(leg, BALL_R)[0] is None, "elongated leg passed as a ball"
    print("segment_ball: OK (err %.1f mm, %d ball pts of %d; leg rejected)"
          % (err * 1000, n, cloud.shape[0]))


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


def test_solve_rig_robust_to_outliers():
    """A fine pass WILL mis-lock occasionally (a leg instead of the ball). The
    plain Kabsch is wrecked by a handful of such pairs; the RANSAC solve must
    shrug them off and still recover the pose to millimetres."""
    world_path = trajectory(150)
    times = np.arange(150) / 30.0
    R_true = random_rotation()
    t_true = np.array([1.4, -0.1, 0.7])
    ref_track, bad_track = [], []
    for k in range(150):
        c_ref = world_path[k]
        c_bad = R_true.dot(c_ref) + t_true
        # 20% of sensor-1 frames are outliers: the lock was on a leg ~0.4-0.9 m
        # away from the true ball centre.
        if k % 5 == 0:
            c_bad = c_bad + RNG.uniform(-1, 1, 3) * 0.6
        ref_track.append((times[k], c_ref + RNG.normal(scale=0.001, size=3)))
        bad_track.append((times[k], c_bad + RNG.normal(scale=0.001, size=3)))
    rig = solve_rig({0: ref_track, 1: bad_track}, ref=0)
    assert 1 in rig, "robust solve dropped the sensor entirely"
    R_est, t_est = rig[1]["R"], rig[1]["t"]
    # solve_rig maps sensor1 -> ref(0). bad = R_true·ref + t_true, so the
    # sensor1->ref map is the INVERSE: (R_true^T, -R_true^T·t_true).
    R_map = R_true.T
    t_map = -R_true.T.dot(t_true)
    rot_err = np.degrees(np.arccos(
        np.clip((np.trace(R_est.T.dot(R_map)) - 1) / 2, -1, 1)))
    t_err = np.linalg.norm(t_est - t_map)
    assert rot_err < 0.6, "outliers corrupted rotation (%.2f deg)" % rot_err
    assert t_err < 0.012, "outliers corrupted translation (%.1f mm)" % (t_err * 1000)
    print("solve_rig robustness: OK (rot %.3f deg, t %.1f mm, %d/%d inliers)"
          % (rot_err, t_err * 1000, rig[1]["pairs"], len(bad_track)))


def test_stationary_sampler():
    """Stop-and-go sampling: hold the ball at several spots (still), moving
    between them. The sampler must commit ONE clean averaged sample per hold per
    camera, and solve_rig on those must recover the pose — even though the two
    cameras are sampled at DIFFERENT instants (the unsynced-rig case)."""
    R1 = random_rotation()
    t1 = np.array([1.2, -0.1, 0.5])
    poses = {0: (np.eye(3), np.zeros(3)), 1: (R1, t1)}
    holds = [np.array(h) for h in [
        (-0.4, 0.9, 0.1), (0.3, 1.1, -0.2), (0.0, 0.7, 0.3),
        (-0.2, 1.3, -0.1), (0.4, 0.8, 0.2), (-0.3, 1.0, -0.3)]]
    # Defaults: whole 0.8 s window must be still (< 8 mm) — so a slow transition
    # is NOT mistaken for a hold. Hold longer than the window below.
    s = StationaryBallSampler(BALL_R, move_dist=0.06, min_samples=3)

    def feed(center_w, t, jitter):
        for sid, (R, tt) in poses.items():
            cam = -R.T.dot(tt)
            cap = sphere_cap(center_w, BALL_R, view_origin=cam, n=200)
            cap = cap.dot(R.T) + tt + RNG.normal(scale=jitter, size=cap.shape)
            # unsynced: the two cameras are fed at slightly different times
            s.add(sid, t + 0.007 * sid, cap)

    t = 0.0
    for hi, h in enumerate(holds):
        for _ in range(32):                     # hold ~1.06 s (> still_window)
            feed(h, t, 0.0008)
            t += 0.033
        if hi + 1 < len(holds):                 # move to the next spot
            for f in range(6):
                feed(h + (holds[hi + 1] - h) * (f + 1) / 6.0, t, 0.001)
                t += 0.033

    assert s.captures >= 5, "only %d holds captured" % s.captures

    # A SLOW continuous drift (a deliberate transition between spots, ~2 cm/s)
    # must NOT be mistaken for a hold — the whole-window stillness test rejects
    # it. This is the "it grabbed while I was still moving" fix.
    s2 = StationaryBallSampler(BALL_R, move_dist=0.06, min_samples=3)
    t2 = 0.0
    for k in range(120):                        # ~4 s of continuous slow motion
        feed_center = np.array([-0.4 + 0.02 * t2, 1.0, 0.0])  # 2 cm/s
        for sid, (R, tt) in poses.items():
            cam = -R.T.dot(tt)
            cap = sphere_cap(feed_center, BALL_R, view_origin=cam, n=200)
            cap = cap.dot(R.T) + tt + RNG.normal(scale=0.0008, size=cap.shape)
            s2.add(sid, t2 + 0.007 * sid, cap)
        t2 += 0.033
    assert s2.captures == 0, "slow drift wrongly captured %d times" % s2.captures
    rig = solve_rig(s.tracks, ref=0, min_pairs=4)
    assert 1 in rig, "stationary solve dropped the sensor"
    R_map, t_map = R1.T, -R1.T.dot(t1)          # sensor1 -> ref is the inverse
    R_est, t_est = rig[1]["R"], rig[1]["t"]
    rot_err = np.degrees(np.arccos(
        np.clip((np.trace(R_est.T.dot(R_map)) - 1) / 2, -1, 1)))
    t_err = np.linalg.norm(t_est - t_map)
    assert rot_err < 1.0, "stationary rotation off %.2f deg" % rot_err
    assert t_err < 0.02, "stationary translation off %.1f mm" % (t_err * 1000)
    print("StationaryBallSampler: OK (%d holds, rot %.3f deg, t %.1f mm)"
          % (s.captures, rot_err, t_err * 1000))


if __name__ == "__main__":
    test_fit_sphere()
    test_segment_ball()
    test_solve_rigid()
    test_pair_tracks()
    test_solve_rig_end_to_end()
    test_solve_rig_robust_to_outliers()
    test_stationary_sampler()
    print("\nALL CALIBRATION TESTS PASSED")
