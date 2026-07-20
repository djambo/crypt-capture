"""Cross-sensor skeleton fusion (central/skeleton_fusion.py):

  - single-sensor passthrough (calibrated or not),
  - noise reduction: 3 agreeing noisy estimates land closer to ground truth
    than the average single sensor,
  - flying-joint rejection: 2 agree + 1 flier -> the flier is dropped,
  - 2-sensor disagreement -> the higher-confidence estimate wins,
  - occlusion completion: a joint seen by only one camera is still emitted,
  - freshness window: a stale sensor stops contributing,
  - registration gate: multiple UNREGISTERED sensors are never mixed
    (no fused output), registered subset wins over unregistered ones,
  - low-confidence estimates are excluded.

Run: python3 -m tests.test_skeleton_fusion
"""

import math

import numpy as np

from central.skeleton_fusion import SkeletonFuser, fuse_joint

RNG = np.random.RandomState(7)


def _dist(a, b):
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def test_single_sensor_passthrough():
    f = SkeletonFuser()
    joints = [(0, (0.1, 1.6, -1.2), 0.9), (9, (0.4, 1.1, -1.1), 0.7)]
    fused = f.add(0, 10.0, joints, registered=False)
    assert fused is not None and f.last_sensors == 1
    assert fused[0] == ((0.1, 1.6, -1.2), 0.9)
    assert fused[9] == ((0.4, 1.1, -1.1), 0.7)
    print("single-sensor passthrough: OK")


def test_noise_reduction():
    f = SkeletonFuser()
    truth = (0.2, 1.5, -1.3)
    single_errs, fused_errs = [], []
    for trial in range(50):
        estimates = []
        for sid in range(3):
            noisy = tuple(truth[i] + RNG.normal(0, 0.03) for i in range(3))
            estimates.append(noisy)
            f.add(sid, 100.0 + trial, [(0, noisy, 0.8)], registered=True)
        fused = f.add(2, 100.0 + trial, [(0, estimates[2], 0.8)],
                      registered=True)
        assert f.last_sensors == 3
        single_errs.extend(_dist(e, truth) for e in estimates)
        fused_errs.append(_dist(fused[0][0], truth))
    mean_single = sum(single_errs) / len(single_errs)
    mean_fused = sum(fused_errs) / len(fused_errs)
    assert mean_fused < mean_single * 0.8, (mean_fused, mean_single)
    print("noise reduction: OK (single %.1f mm -> fused %.1f mm)"
          % (mean_single * 1000, mean_fused * 1000))


def test_flying_joint_rejected():
    # Two cameras agree on the wrist; the third's depth pixel hit the wall
    # 1.5 m behind — the classic flying joint, WITH confident scores.
    good = [((0.40, 1.10, -1.20), 0.8), ((0.42, 1.12, -1.21), 0.7)]
    flier = ((0.90, 1.40, -2.70), 0.9)          # highest confidence!
    fused = fuse_joint(good + [flier])
    assert _dist(fused[0], (0.41, 1.11, -1.205)) < 0.05, fused
    # And via the full fuser:
    f = SkeletonFuser()
    f.add(0, 5.0, [(9, good[0][0], good[0][1])], registered=True)
    f.add(1, 5.0, [(9, good[1][0], good[1][1])], registered=True)
    fused = f.add(2, 5.0, [(9, flier[0], flier[1])], registered=True)
    assert _dist(fused[9][0], (0.41, 1.11, -1.205)) < 0.05, fused
    print("flying-joint rejection: OK")


def test_two_sensor_disagreement():
    # With only two estimates there's no majority: the higher-confidence one
    # wins outright when they disagree beyond the outlier radius.
    a = ((0.4, 1.1, -1.2), 0.9)
    b = ((0.9, 1.4, -2.7), 0.3)
    fused = fuse_joint([a, b])
    assert fused == a
    # Agreeing pair -> weighted mean between them.
    c = ((0.42, 1.12, -1.22), 0.6)
    fused = fuse_joint([a, c])
    assert _dist(fused[0], a[0]) < _dist(a[0], c[0])
    assert fused[1] == 0.9
    print("2-sensor disagreement: OK")


def test_occlusion_completion():
    f = SkeletonFuser()
    f.add(0, 5.0, [(0, (0.1, 1.6, -1.2), 0.9)], registered=True)
    fused = f.add(1, 5.0, [(0, (0.12, 1.61, -1.19), 0.8),
                           (10, (0.5, 1.0, -1.1), 0.7)], registered=True)
    assert 0 in fused and 10 in fused           # wrist seen by only camera 1
    assert fused[10] == ((0.5, 1.0, -1.1), 0.7)
    print("occlusion completion: OK")


def test_freshness_window():
    f = SkeletonFuser(window_s=0.4)
    f.add(0, 5.0, [(0, (9.0, 9.0, 9.0), 0.9)], registered=True)
    fused = f.add(1, 6.0, [(0, (0.1, 1.6, -1.2), 0.8)], registered=True)
    assert f.last_sensors == 1                  # sensor 0 aged out
    assert fused[0] == ((0.1, 1.6, -1.2), 0.8)
    print("freshness window: OK")


def test_registration_gate():
    # Two fresh UNREGISTERED sensors: their "world" frames don't align, so
    # fusing would mix incompatible frames -> no fused output at all.
    f = SkeletonFuser()
    f.add(0, 5.0, [(0, (0.1, 1.6, -1.2), 0.9)], registered=False)
    fused = f.add(1, 5.0, [(0, (1.5, 1.6, -0.2), 0.9)], registered=False)
    assert fused is None and f.last_sensors == 0
    # A registered subset exists -> fuse ONLY it (the unregistered sensor's
    # frame is excluded, not averaged in).
    fused = f.add(2, 5.0, [(0, (0.2, 1.55, -1.25), 0.8)], registered=True)
    assert fused is not None and f.last_sensors == 1
    assert fused[0] == ((0.2, 1.55, -1.25), 0.8)
    print("registration gate: OK")


def test_low_confidence_excluded():
    f = SkeletonFuser(min_conf=0.05)
    f.add(0, 5.0, [(0, (9.0, 9.0, 9.0), 0.01)], registered=True)
    fused = f.add(1, 5.0, [(0, (0.1, 1.6, -1.2), 0.8)], registered=True)
    assert fused[0] == ((0.1, 1.6, -1.2), 0.8)
    # A sensor whose every joint is below the floor contributes nothing.
    f2 = SkeletonFuser()
    fused = f2.add(0, 5.0, [(0, (1.0, 1.0, 1.0), 0.01)], registered=True)
    assert fused is None
    print("low-confidence exclusion: OK")


def test_on_pose_fused_broadcast():
    """The real relay wiring (_on_pose): per-sensor skeleton broadcasts are
    unchanged, and a fused message rides alongside — passthrough for one
    sensor, gated off for two unregistered sensors, back on (n=2, averaged)
    once the rig registers them."""
    import os
    import tempfile
    from central.preview_server import PreviewServer
    with tempfile.TemporaryDirectory() as d:
        srv = PreviewServer(rig_calib=os.path.join(d, "rig.json"))
        msgs = []
        srv._broadcast_text = lambda obj: (
            msgs.append(obj) if obj.get("type") == "skeleton" else None)
        srv._ray_table(0, 640, 576)
        srv._ray_table(1, 640, 576)
        kp = [(0, 320.0, 288.0, 1.5, 0.9)]     # centre pixel, 1.5 m
        # One sensor: per-sensor + fused passthrough.
        srv._on_pose({"sensor_id": 0, "keypoints": kp})
        kinds = [m["sensor"] for m in msgs]
        assert kinds == [0, "fused"], kinds
        assert msgs[1]["n"] == 1
        assert msgs[1]["joints"]["0"][:3] == msgs[0]["joints"]["0"][:3]
        # A second, UNREGISTERED sensor joins: fused output stops (frames
        # don't align), per-sensor messages continue.
        del msgs[:]
        srv._on_pose({"sensor_id": 1, "keypoints": kp})
        srv._on_pose({"sensor_id": 0, "keypoints": kp})
        kinds = [m["sensor"] for m in msgs]
        assert "fused" not in kinds and kinds.count(0) == 1 \
            and kinds.count(1) == 1, kinds
        # Rig registers both -> fused resumes with n=2.
        eye = (np.eye(3, dtype=np.float32), np.zeros(3, dtype=np.float32))
        srv._rig = {0: eye, 1: eye}
        del msgs[:]
        srv._on_pose({"sensor_id": 1, "keypoints": kp})
        srv._on_pose({"sensor_id": 0, "keypoints": kp})
        fused = [m for m in msgs if m["sensor"] == "fused"]
        assert fused and fused[-1]["n"] == 2, msgs
        j = fused[-1]["joints"]["0"]
        assert abs(j[2] + 1.5) < 0.05, j       # 1.5 m depth -> view z = -1.5
    print("_on_pose fused broadcast: OK")


def test_drop_sensor():
    f = SkeletonFuser()
    f.add(0, 5.0, [(0, (9.0, 9.0, 9.0), 0.9)], registered=True)
    f.drop_sensor(0)
    fused = f.add(1, 5.0, [(0, (0.1, 1.6, -1.2), 0.8)], registered=True)
    assert f.last_sensors == 1
    assert fused[0] == ((0.1, 1.6, -1.2), 0.8)
    print("drop_sensor: OK")


if __name__ == "__main__":
    test_single_sensor_passthrough()
    test_noise_reduction()
    test_flying_joint_rejected()
    test_two_sensor_disagreement()
    test_occlusion_completion()
    test_freshness_window()
    test_registration_gate()
    test_low_confidence_excluded()
    test_on_pose_fused_broadcast()
    test_drop_sensor()
    print("\nALL SKELETON FUSION TESTS PASSED")
